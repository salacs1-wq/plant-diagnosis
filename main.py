import os
import re
import platform
import traceback
from typing import Any, Dict, List, Optional, Tuple

import anyio
import httpx
from fastapi import FastAPI, Body, File, HTTPException, Request, UploadFile, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware


# =========================================================
# CONFIG
# =========================================================
APP_VERSION = os.getenv("APP_VERSION", "1.2.3").strip()

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_PROJECT = os.getenv("PLANTNET_PROJECT", "k-middle-europe").strip()  # default: közép-európa
PLANTNET_BASE_URL = os.getenv("PLANTNET_BASE_URL", "https://my-api.plantnet.org").rstrip("/")

DEFAULT_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "45"))
DEFAULT_CONNECT_TIMEOUT = float(os.getenv("HTTP_CONNECT_TIMEOUT", "15"))

# Pest módban: ha issueScore ez alatt van, ne mutassunk "kórkép-kód" listát (fantom zajt csökkent)
PEST_MIN_ISSUE_SCORE = float(os.getenv("PEST_MIN_ISSUE_SCORE", "0.45"))

DATA_URL_RE = re.compile(
    r"^data:(?P<mime>[^;]+);base64,(?P<data>.+)$",
    re.IGNORECASE | re.DOTALL,
)

app = FastAPI(
    title="Növénydiagnosztikai API (PlantNet proxy)",
    version=APP_VERSION,
    description=(
        "PlantNet proxy API.\n\n"
        "Fontos: a /v2/diseases/identify által visszaadott 4–8 betűs 'kódok' "
        "NEM hivatalos kórtani azonosítók (csak jelzések/döntéstámogatás). "
        "A végső szakmai döntést a felhasználó (növényorvos) hozza."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# GLOBAL ERROR HANDLER
# =========================================================
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "error": "internal_server_error",
                "type": exc.__class__.__name__,
                "message": str(exc),
                "path": str(request.url.path),
                "trace": traceback.format_exc().splitlines()[-25:],
            }
        },
    )


# =========================================================
# HELPERS
# =========================================================
def _require_api_key() -> None:
    if not PLANTNET_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Hiányzik a PLANTNET_API_KEY környezeti változó (Render -> Environment).",
        )


def _httpx_timeout() -> httpx.Timeout:
    return httpx.Timeout(DEFAULT_TIMEOUT, connect=DEFAULT_CONNECT_TIMEOUT)


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return resp.text


def _guess_mime(filename: str, fallback: str = "image/jpeg") -> str:
    name = (filename or "").lower()
    if name.endswith(".png"):
        return "image/png"
    if name.endswith(".webp"):
        return "image/webp"
    if name.endswith(".jpg") or name.endswith(".jpeg"):
        return "image/jpeg"
    return fallback


def _normalize_organs_for_images(organs: Optional[str], image_count: int) -> Optional[List[str]]:
    """
    PlantNet 'organs' paraméter projektenként/kontextusban kényes lehet.
    Alapból NEM küldjük. Csak ha explicit meg van adva.
    """
    if organs is None:
        return None
    organ = (organs or "").strip().lower()
    if not organ or organ in ("auto", "null", "none"):
        return None
    return [organ] * max(1, image_count)


def _compact_species_response(raw: Dict[str, Any], *, project: str, organs_sent: Optional[str]) -> Dict[str, Any]:
    results = raw.get("results") or []
    top_matches: List[Dict[str, Any]] = []
    best = None
    best_score = None

    for r in results[:5]:
        score = r.get("score")
        species = (r.get("species") or {})
        sci = (
            species.get("scientificNameWithoutAuthor")
            or species.get("scientificName")
            or species.get("scientificNameAuthorship")
        )
        if not sci:
            continue
        if best is None:
            best = sci
            best_score = score
        top_matches.append({"name": sci, "score": float(score) if score is not None else None})

    top1 = float(best_score) if best_score is not None else None
    return {
        "bestMatch": best or "ismeretlen",
        "confidence": {"top1_score": top1, "level": "species"},
        # max 5 (ha PlantNet csak 3-at ad, akkor 3 marad)
        "topMatches": top_matches[:5],
        "meta": {"project": project, "organs_sent": organs_sent},
    }


def _is_code_like(s: str) -> bool:
    """
    Egyszerű heurisztika: 4–8 nagybetűs/ szám kód jelleg.
    """
    if not s:
        return False
    ss = s.strip()
    if len(ss) < 4 or len(ss) > 8:
        return False
    return all(ch.isupper() or ch.isdigit() or ch == "_" for ch in ss)


def _compact_disease_response(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    PlantNet diseases endpoint kimenete eltérhet.
    Itt 'bestMatch' lehet kód (pl. SEPTTR, FUSAME).
    Magyarítás: ha nincs mapping, jelezzük, hogy kód.
    """
    results = raw.get("results") or []
    top_matches: List[Dict[str, Any]] = []
    best = None
    best_score = None

    for r in results[:10]:  # többet beolvasunk, de max 5-öt adunk vissza
        score = r.get("score")
        disease = (r.get("disease") or {})
        pest = (r.get("pest") or {})

        code_or_name = (
            disease.get("code")
            or disease.get("name")
            or pest.get("code")
            or pest.get("name")
            or r.get("code")
            or r.get("name")
        )
        if not code_or_name:
            continue

        name = str(code_or_name).strip()
        if best is None:
            best = name
            best_score = score

        top_matches.append(
            {
                "name": name,
                "name_hu": f"{name} – (kód, fordítás nincs)" if _is_code_like(name) else name,
                "score": float(score) if score is not None else None,
            }
        )

    top_matches = top_matches[:5]
    top1 = float(best_score) if best_score is not None else None

    best_hu = None
    if best:
        best_hu = f"{best} – (kód, fordítás nincs)" if _is_code_like(best) else best

    return {
        "bestMatch": best or "ismeretlen",
        "bestMatch_hu": best_hu or "ismeretlen",
        "confidence": {"top1_score": top1, "level": "disease_or_pest"},
        "topMatches": top_matches,
        "meta": {"non_official_codes": True},
    }


# =========================================================
# SYNC HTTP HELPERS (run in thread)
# =========================================================
def _sync_get(url: str) -> httpx.Response:
    with httpx.Client(timeout=_httpx_timeout(), follow_redirects=True) as client:
        return client.get(url)


def _sync_post(url: str, params: Dict[str, Any], data, files) -> httpx.Response:
    with httpx.Client(timeout=_httpx_timeout(), follow_redirects=True) as client:
        return client.post(url, params=params, data=data, files=files)


async def _download_bytes(url: str) -> bytes:
    if not url or not isinstance(url, str):
        raise HTTPException(status_code=422, detail="Hiányzik: download_link")

    r = await anyio.to_thread.run_sync(_sync_get, url)

    if r.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"stage": "download", "status": r.status_code, "text": r.text[:500]},
        )

    data = r.content
    if not data or len(data) < 50:
        raise HTTPException(status_code=422, detail="A letöltött kép üres vagy túl kicsi.")
    return data


# =========================================================
# PLANTNET CALLS (sync in thread)
# =========================================================
async def _plantnet_identify(
    images: List[Tuple[str, bytes, str]],
    *,
    project: str,
    organs: Optional[str],
) -> Dict[str, Any]:
    _require_api_key()
    if not images:
        raise HTTPException(status_code=422, detail="Nincs kép az azonosításhoz.")

    url = f"{PLANTNET_BASE_URL}/v2/identify/{project}"

    files = [("images", (fn or "image.jpg", b, mt or "image/jpeg")) for (fn, b, mt) in images]

    # organs csak akkor megy ki, ha explicit megadták
    organs_list = _normalize_organs_for_images(organs, len(images))
    data = None
    if organs_list is not None:
        data = [("organs", o) for o in organs_list]

    params = {"api-key": PLANTNET_API_KEY}

    r = await anyio.to_thread.run_sync(_sync_post, url, params, data, files)

    if r.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={
                "plantnet_stage": "identify",
                "plantnet_status": r.status_code,
                "plantnet_error": _safe_json(r),
            },
        )

    return r.json()


async def _plantnet_diseases_identify(image: Tuple[str, bytes, str]) -> Dict[str, Any]:
    _require_api_key()

    # diseases endpoint NEM fogad project/organs paramétert
    url = f"{PLANTNET_BASE_URL}/v2/diseases/identify"
    params = {"api-key": PLANTNET_API_KEY}
    files = [("images", (image[0] or "image.jpg", image[1], image[2] or "image/jpeg"))]

    r = await anyio.to_thread.run_sync(_sync_post, url, params, None, files)

    if r.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={
                "plantnet_stage": "diseases",
                "plantnet_status": r.status_code,
                "plantnet_error": _safe_json(r),
            },
        )

    return r.json()


# =========================================================
# ROUTES: health/version
# =========================================================
@app.get("/")
async def root() -> Dict[str, Any]:
    return {"status": "ok"}


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok"}


@app.get("/version")
async def version() -> Dict[str, Any]:
    return {"version": APP_VERSION, "python": platform.python_version(), "httpx": httpx.__version__}


# =========================================================
# FILE UPLOAD ENDPOINTS (Swagger/Chrome tesztre)
# =========================================================
@app.post("/identify")
async def identify(
    image: UploadFile = File(...),
    project: str = Query(default=PLANTNET_PROJECT),
    organs: Optional[str] = Query(default=None),
):
    img_bytes = await image.read()
    if not img_bytes or len(img_bytes) < 50:
        raise HTTPException(status_code=422, detail="A feltöltött kép üres vagy túl kicsi.")

    mime = image.content_type or _guess_mime(image.filename or "image.jpg")
    images = [(image.filename or "image.jpg", img_bytes, mime)]

    raw = await _plantnet_identify(images, project=project, organs=organs)
    plant = _compact_species_response(raw, project=project, organs_sent=(organs if organs else None))
    return {"plant": plant}


@app.post("/diagnose")
async def diagnose(
    image: UploadFile = File(...),
    project: str = Query(default=PLANTNET_PROJECT),
    organs: Optional[str] = Query(default=None),
    caseType: str = Query(default="weed"),
    mode: str = Query(default="learning"),
):
    """
    Diagnose file upload: caseType alapján futtatjuk (weed/disease/pest).
    """
    img_bytes = await image.read()
    if not img_bytes or len(img_bytes) < 50:
        raise HTTPException(status_code=422, detail="A feltöltött kép üres vagy túl kicsi.")

    case_type = (caseType or "weed").strip().lower()
    if case_type not in ("weed", "disease", "pest"):
        case_type = "weed"

    mode_ = (mode or "learning").strip().lower()
    if mode_ not in ("learning", "expert"):
        mode_ = "learning"

    mime = image.content_type or _guess_mime(image.filename or "image.jpg")
    images = [(image.filename or "image.jpg", img_bytes, mime)]

    return await _diagnose_core(
        images=images,
        project=project,
        organs=organs,
        mode=mode_,
        case_type=case_type,
    )


# =========================================================
# CORE LOGIC: 3 utak (weed/disease/pest)
# =========================================================
async def _diagnose_core(
    *,
    images: List[Tuple[str, bytes, str]],
    project: str,
    organs: Optional[str],
    mode: str,
    case_type: str,
) -> Dict[str, Any]:
    # weed   -> csak növény
    # disease-> kórkép + növény háttér
    # pest   -> kártevő/kártétel jelzés (diseases endpoint), de szűrjük a zajt score alapján

    if case_type == "weed":
        plant_raw = await _plantnet_identify(images, project=project, organs=organs)
        plant = _compact_species_response(plant_raw, project=project, organs_sent=(organs if organs else None))

        summary = {
            "bestPlant": plant["bestMatch"],
            "plantScore": plant["confidence"]["top1_score"],
            "topPlants": plant["topMatches"],
            "bestIssue": "n/a",
            "bestIssue_hu": None,
            "issueScore": None,
            "topIssues": [],
            "project": project,
            "organsSent": (organs if organs else None),
            "mode": mode,
            "caseType": case_type,
            "imageCount": len(images),
            "notes": ["weed mód: csak növényazonosítás fut"],
        }
        return {"plant": plant, "diseaseOrPest": None, "summary": summary}

    # disease / pest: diseases endpoint 1 képpel (az első kép)
    disease_raw = await _plantnet_diseases_identify(images[0])
    disease = _compact_disease_response(disease_raw)

    # növény háttér (segít a kontextusban)
    plant_raw = await _plantnet_identify(images, project=project, organs=organs)
    plant = _compact_species_response(plant_raw, project=project, organs_sent=(organs if organs else None))

    # PEST szűrő: ha alacsony a score, ne listázzunk kódokat (fantom zaj)
    if case_type == "pest":
        issue_score = disease["confidence"]["top1_score"] or 0.0
        if issue_score < PEST_MIN_ISSUE_SCORE:
            disease = {
                "bestMatch": "n/a",
                "bestMatch_hu": "nincs elég biztos kártevő/kártétel jelzés",
                "confidence": {"top1_score": None, "level": "disease_or_pest"},
                "topMatches": [],
                "meta": {"filtered": True, "reason": f"issueScore<{PEST_MIN_ISSUE_SCORE}"},
            }

    notes = []
    if case_type in ("disease", "pest"):
        notes.append("A kórkép/kártevő modul jelzéseket ad; a 4–8 betűs kódok nem hivatalosak.")

    summary = {
        "bestPlant": plant["bestMatch"],
        "plantScore": plant["confidence"]["top1_score"],
        "topPlants": plant["topMatches"],
        "bestIssue": disease["bestMatch"],
        "bestIssue_hu": disease.get("bestMatch_hu"),
        "issueScore": disease["confidence"]["top1_score"],
        "topIssues": disease["topMatches"],
        "project": project,
        "organsSent": (organs if organs else None),
        "mode": mode,
        "caseType": case_type,
        "imageCount": len(images),
        "notes": notes,
    }
    return {"plant": plant, "diseaseOrPest": disease, "summary": summary}


# =========================================================
# OpenAI file refs endpoint (Actions-hez)
# =========================================================
@app.post("/diagnose_files")
async def diagnose_files(payload: Dict[str, Any] = Body(...)):
    refs = payload.get("openaiFileIdRefs") or []
    if not isinstance(refs, list) or len(refs) < 1:
        raise HTTPException(status_code=422, detail="Hiányzik: openaiFileIdRefs (min 1 kép).")
    if len(refs) > 5:
        raise HTTPException(status_code=422, detail="Túl sok kép. Max 5 kép / eset.")

    # organs: alapból NEM küldjük
    organs_raw = payload.get("organs", None)
    organs = None
    if isinstance(organs_raw, str):
        o = organs_raw.strip().lower()
        if o and o not in ("auto", "none", "null"):
            organs = o

    mode = (payload.get("mode") or "learning").strip().lower()
    if mode not in ("learning", "expert"):
        mode = "learning"

    case_type = (payload.get("caseType") or "weed").strip().lower()
    if case_type not in ("weed", "disease", "pest"):
        case_type = "weed"

    project = (payload.get("project") or PLANTNET_PROJECT).strip() or PLANTNET_PROJECT

    images: List[Tuple[str, bytes, str]] = []
    for idx, r in enumerate(refs):
        if not isinstance(r, dict):
            continue
        dl = r.get("download_link")
        name = r.get("name") or f"image_{idx+1}.jpg"
        mime = r.get("mime_type") or _guess_mime(name)
        data = await _download_bytes(dl)
        images.append((name, data, mime))

    if not images:
        raise HTTPException(status_code=422, detail="Nem sikerült letölteni a képeket (üres lista).")

    return await _diagnose_core(
        images=images,
        project=project,
        organs=organs,
        mode=mode,
        case_type=case_type,
    )
