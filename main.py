from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse

app = FastAPI(
    title="Plant Diagnosis API",
    version="1.0.0",
    description="Kép-alapú növény/gyom/betegség diagnosztika proxy (PlantNet).",
)

# -----------------------------
# CONFIG
# -----------------------------
# Renderen: Settings -> Environment -> add PLANTNET_API_KEY
PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()

# Alap projekt: ezt szeretted, mert jobb a score
DEFAULT_PROJECT = "k-middle-europe"  # közép-európa

# PlantNet base endpoint (v2/identify/{project})
PLANTNET_BASE = "https://my-api.plantnet.org/v2/identify"

# Gyom-szűrés (gyom módban csak ezeket engedjük első körben)
# (Ha üres találat lenne, visszaadjuk az eredeti top5-öt is)
WEED_GENUS_ALLOWLIST = {
    # Egyszikűek (kalászos + kukorica kritikus)
    "Lolium", "Apera", "Alopecurus", "Bromus", "Poa", "Vulpia",
    "Setaria", "Echinochloa", "Digitaria", "Panicum", "Sorghum",
    "Cynodon", "Elymus", "Agropyron", "Agrostis", "Phragmites",
    "Calamagrostis", "Festuca", "Avena", "Phalaris",

    # Kétszikűek (gyakori gyomok 5 kultúrában)
    "Stellaria", "Veronica", "Capsella", "Lamium", "Viola", "Galium",
    "Matricaria", "Tripleurospermum", "Papaver", "Sinapis", "Raphanus",
    "Chenopodium", "Amaranthus", "Cirsium", "Convolvulus",
    "Ambrosia", "Xanthium", "Datura", "Abutilon", "Polygonum",
    "Persicaria", "Fallopia", "Rumex",
}

def _safe_float(x: Any) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None

def _extract_genus(scientific_name: str) -> str:
    # pl. "Lolium perenne" -> "Lolium"
    # pl. "Bromus spp." -> "Bromus"
    s = (scientific_name or "").strip()
    if not s:
        return ""
    return s.split()[0]

def _plantnet_to_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    PlantNet response -> egységesített lista:
    [
      {scientific_name, score, common_names[], gbif_id?, source="PlantNet"}
    ]
    """
    results = payload.get("results") or []
    rows: List[Dict[str, Any]] = []
    for r in results:
        score = _safe_float(r.get("score"))
        sp = r.get("species") or {}
        sci = sp.get("scientificNameWithoutAuthor") or sp.get("scientificName") or ""
        common = sp.get("commonNames") or []
        gbif = sp.get("gbif", {}).get("id") if isinstance(sp.get("gbif"), dict) else None
        rows.append(
            {
                "scientific_name": sci,
                "score": score,
                "common_names": common,
                "gbif_id": gbif,
                "source": "PlantNet",
            }
        )
    # score csökkenő
    rows.sort(key=lambda x: (x["score"] is not None, x["score"]), reverse=True)
    return rows

def _filter_weeds(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], bool]:
    """
    Gyom-szűrés: csak allowlist-ben lévő genusok.
    Visszaad: (filtered_rows, did_filter)
    """
    filtered: List[Dict[str, Any]] = []
    for row in rows:
        genus = _extract_genus(row.get("scientific_name", ""))
        if genus in WEED_GENUS_ALLOWLIST:
            filtered.append(row)

    # Ha semmi nem maradt, NE dobjunk el mindent: inkább hagyjuk meg az eredetit
    if not filtered:
        return rows[:], False
    return filtered, True

async def _call_plantnet_identify(
    *,
    project: str,
    image_bytes: bytes,
    filename: str,
    content_type: str,
) -> Dict[str, Any]:
    if not PLANTNET_API_KEY:
        raise RuntimeError("Hiányzik a PLANTNET_API_KEY környezeti változó a szerveren.")

    url = f"{PLANTNET_BASE}/{project}"

    # FONTOS: k-middle-europe endpoint NEM engedi az 'organs' paramétert.
    params = {"api-key": PLANTNET_API_KEY}

    # PlantNet több fájlt is tud, de nekünk most elég 1 kép.
    files = [("images", (filename, image_bytes, content_type or "image/jpeg"))]

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, params=params, files=files)
        if resp.status_code != 200:
            # Adjunk vissza értelmes hibát
            raise RuntimeError(f"PlantNet HTTP hiba: {resp.status_code} / {resp.text[:500]}")
        return resp.json()

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/diagnose")
async def diagnose(
    file: UploadFile = File(..., description="Kép (jpg/png/webp)"),
    caseType: str = Form(..., description="weed | disease | pest | symptom"),
    project: str = Form(DEFAULT_PROJECT, description="PlantNet projekt azonosító (alap: k-middle-europe)"),
    mode: str = Form("expert", description="expert | simple (jelenleg csak tároljuk)"),
):
    """
    Diagnózis:
    - Gyom módban: PlantNet találatokból kiszűrjük a nem gyom fajokat (genus allowlist).
    - Betegség/kártevő módban: egyelőre PlantNet csak 'növény' találatot ad, itt még nem végleges.
    """
    try:
        img_bytes = await file.read()
        if not img_bytes:
            return JSONResponse(status_code=400, content={"error": "Üres fájl."})

        use_project = (project or DEFAULT_PROJECT).strip() or DEFAULT_PROJECT

        plantnet_payload = await _call_plantnet_identify(
            project=use_project,
            image_bytes=img_bytes,
            filename=file.filename or "image.jpg",
            content_type=file.content_type or "image/jpeg",
        )

        rows = _plantnet_to_rows(plantnet_payload)

        # top10-ig dolgozunk, hogy a szűrésnek legyen tere
        rows = rows[:10]

        did_filter = False
        filtered_rows = rows
        if (caseType or "").strip().lower() == "weed":
            filtered_rows, did_filter = _filter_weeds(rows)

        # top5 vissza
        top5 = filtered_rows[:5]

        return {
            "ok": True,
            "caseType": caseType,
            "project": use_project,
            "mode": mode,
            "filtered_non_weeds": did_filter,
            "top5": top5,
            "raw_count": len(rows),
            "note": (
                "Gyom módban genus-alapú gyom-szűrés aktív."
                if did_filter
                else "Gyom-szűrés nem talált allowlist gyomot, eredeti top találatok maradtak."
                if (caseType or "").strip().lower() == "weed"
                else "Növény-azonosítás PlantNet alapján."
            ),
        }

    except Exception as e:
        return JSONResponse(status_code=502, content={"error": "Diagnózis hiba", "detail": str(e)})
