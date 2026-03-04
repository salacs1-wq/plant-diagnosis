import os
from typing import Optional, List, Dict, Any

import requests
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware


# =========================
# Config
# =========================
PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()

# PlantNet base (NE legyen benne /identify/{project}; csak a base kell)
PLANTNET_BASE = os.getenv("PLANTNET_BASE", "https://my-api.plantnet.org/v2").rstrip("/")

# FIX: Közép-Európa projekt (jobb score nálatok)
DEFAULT_PROJECT = "weurope"
DEFAULT_MODE = "expert"
DEFAULT_TOPK = int(os.getenv("DEFAULT_TOPK", "5"))

# 45% alatt kérdezzen rá
CONFIRM_THRESHOLD = float(os.getenv("CONFIRM_THRESHOLD", "0.45"))


# =========================
# Magyar név szótár (bővíthető)
# kulcs: normalizált (kisbetű) latin név
# =========================
HU_NAME: Dict[str, str] = {
    # Kalászos kritikus egyszikűek
    "lolium multiflorum": "olasz perje",
    "lolium perenne": "angolperje",
    "alopecurus myosuroides": "szántóföldi ecsetpázsit",
    "apera spica-venti": "szélfű",
    "vulpia myuros": "egérfarkfű",
    "poa annua": "egynyári perje",
    "poa trivialis": "réti perje",
    "bromus sterilis": "meddő rozsnok",
    "bromus secalinus": "rozsnok",
    "bromus tectorum": "tetőrozsnok",

    # Kukorica egyszikűek (amit mondtál: Setaria, kakasláb, Sorghum)
    "setaria viridis": "zöld muhar",
    "setaria pumila": "sárga muhar",
    "echinochloa crus-galli": "kakaslábfű",
    "panicum miliaceum": "köles",
    "digitaria sanguinalis": "vérmuhar",
    "digitaria ischaemum": "homoki muhar",
    "sorghum halepense": "fenyércirok",
    "elymus repens": "tarackbúza",
    "cynodon dactylon": "csillagpázsit",
}

# “spp.” csoportnevek gyors magyarítása
GENUS_HU: Dict[str, str] = {
    "bromus": "rozsnok fajok",
    "lolium": "perje fajok",
    "poa": "perje fajok",
    "setaria": "muhar fajok",
    "digitaria": "muhar fajok",
    "panicum": "kölesfélék",
    "sorghum": "cirokfélék",
}


# =========================
# App
# =========================
app = FastAPI(title="Plant Diagnose API", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True}


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else (1.0 if x > 1 else x)


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _extract_best_name(result_item: Dict[str, Any]) -> str:
    sp = result_item.get("species", {}) or {}
    return (
        sp.get("scientificNameWithoutAuthor")
        or sp.get("scientificName")
        or sp.get("genus", {}).get("scientificName")
        or "Unknown"
    )


def _extract_score(result_item: Dict[str, Any]) -> float:
    try:
        return _clamp01(float(result_item.get("score", 0.0)))
    except Exception:
        return 0.0


def hu_common_name(scientific_name: str) -> Optional[str]:
    """
    Magyarítás:
      1) pontos fajnév a szótárból
      2) genus + spp. esetben nem hazudunk fajra, csak “rozsnok fajok”
      3) ha nem tudjuk, None (később bővíthető)
    """
    s = _norm(scientific_name)

    # kezeljük: "Bromus spp." / "Bromus sp." / "Bromus spp"
    if " spp" in s or s.endswith(" sp.") or s.endswith(" sp"):
        genus = s.split()[0]
        return GENUS_HU.get(genus, None)

    # pontos találat
    if s in HU_NAME:
        return HU_NAME[s]

    # néha van szerzőnév / zárójel / stb → vágjuk első két tagra
    parts = s.replace(",", " ").split()
    if len(parts) >= 2:
        maybe_binom = f"{parts[0]} {parts[1]}"
        if maybe_binom in HU_NAME:
            return HU_NAME[maybe_binom]

    return None


def call_plantnet_identify(
    img_bytes: bytes,
    filename: str,
    content_type: str,
    project: str,
    organs: Optional[str],
) -> Dict[str, Any]:
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="Hiányzik a PLANTNET_API_KEY környezeti változó.")

    url = f"{PLANTNET_BASE}/identify/{project}"
    params = {"api-key": PLANTNET_API_KEY}
    data = {}
    if organs:
        data["organs"] = organs

    files = {
        "images": (filename or "image.jpg", img_bytes, content_type or "image/jpeg")
    }

    r = requests.post(url, params=params, data=data, files=files, timeout=60)
    if r.status_code != 200:
        raise HTTPException(
            status_code=500,
            detail=f"PlantNet hiba ({r.status_code}): {r.text[:300]}",
        )
    return r.json()


def auto_organs(case_type: str, organs: Optional[str]) -> Optional[str]:
    """
    Endpoint finomhangolás:
    - weed: leaf (általában)
    - disease: leaf
    - pest: nem rovar-API → host növényhez leaf
    """
    if organs and organs.strip():
        return organs.strip()
    if case_type in ("weed", "disease", "pest"):
        return "leaf"
    return None


@app.post("/diagnose")
async def diagnose(
    image: UploadFile = File(...),
    caseType: str = Form(...),       # weed / disease / pest
    organs: Optional[str] = Form(None),
    topK: int = Form(DEFAULT_TOPK),
):
    if caseType not in ("weed", "disease", "pest"):
        raise HTTPException(status_code=400, detail="caseType csak weed/disease/pest lehet.")
    if topK < 1 or topK > 10:
        raise HTTPException(status_code=400, detail="topK 1 és 10 között legyen.")

    img_bytes = await image.read()
    if not img_bytes:
        raise HTTPException(status_code=400, detail="Üres kép fájl.")

    project = DEFAULT_PROJECT
    mode = DEFAULT_MODE
    organs2 = auto_organs(caseType, organs)

    plantnet_json = call_plantnet_identify(
        img_bytes=img_bytes,
        filename=image.filename or "image.jpg",
        content_type=image.content_type or "image/jpeg",
        project=project,
        organs=organs2,
    )

    results: List[Dict[str, Any]] = (plantnet_json.get("results") or [])[:topK]

    candidates = []
    for idx, item in enumerate(results, start=1):
        sci = _extract_best_name(item)
        sc = _extract_score(item)
        candidates.append(
            {
                "rank": idx,
                "scientificName": sci,
                "commonNameHu": hu_common_name(sci),
                "plantnetScore": sc,
                "gptScore": sc,  # fix: 1:1
            }
        )

    top1 = candidates[0]["plantnetScore"] if candidates else 0.0
    needs_confirmation = bool(candidates) and (top1 < CONFIRM_THRESHOLD)

    reason = None
    if needs_confirmation:
        reason = (
            f"Az első találat pontszáma alacsony ({top1*100:.1f}%). "
            f"Kérlek csinálj még 1-2 képet: (1) levélhüvely+nyelvecske/fülecske (ligula/auricula), "
            f"(2) teljes növény, (3) ha van: bugavirágzat."
        )

    note = None
    if caseType == "pest":
        note = (
            "Megjegyzés: a PlantNet növényazonosító, nem rovartan API. "
            "Pest módban most a gazdanövény azonosítására használjuk; kártevőhöz külön rovartan forrás/API kell."
        )

    return {
        "caseType": caseType,
        "project": project,
        "mode": mode,
        "topK": topK,
        "candidates": candidates,
        "needsConfirmation": needs_confirmation,
        "confirmationReasonHu": reason,
        "noteHu": note,
    }
