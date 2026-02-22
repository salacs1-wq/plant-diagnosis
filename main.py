import os
import platform
import traceback
from typing import Any, Dict, List, Optional, Tuple

import anyio
import httpx
from fastapi import FastAPI, Body, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse


# ----------------------------
# Config
# ----------------------------
APP_VERSION = os.getenv("APP_VERSION", "1.2.3").strip()

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()

# ✅ Default: weurope (Közép-Európa app módhoz konzisztens)
PLANTNET_PROJECT = os.getenv("PLANTNET_PROJECT", "weurope").strip()

PLANTNET_BASE_URL = os.getenv("PLANTNET_BASE_URL", "https://my-api.plantnet.org").rstrip("/")

DEFAULT_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "45"))
DEFAULT_CONNECT_TIMEOUT = float(os.getenv("HTTP_CONNECT_TIMEOUT", "15"))

app = FastAPI(
    title="Növénydiagnosztikai API (PlantNet proxy)",
    version=APP_VERSION,
    description="PlantNet proxy /diagnose_files endpointdal (1–5 kép).",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------------
# Global error handler
# ----------------------------
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


# ----------------------------
# Helpers
# ----------------------------
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


def _normalize_organs_for_images(organs: str, image_count: int) -> List[str]:
    organ = (organs or "").strip().lower()
    if not organ:
        organ = "leaf"
    return [organ] * max(1, image_count)


def _compact_species_response(
    raw: Dict[str, Any],
    *,
    project: str,
    organs_sent: Optional[str],
) -> Dict[str, Any]:
    """
    ✅ Top3 és score 1:1 PlantNet results[].score alapján.
    """
    results = raw.get("results") or []
    top_matches: List[Dict[str, Any]] = []

    best = "ismeretlen"
    best_score: Optional[float] = None

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

        s = float(score) if score is not None else None
        top_matches.append({"name": sci, "score": s})

        if best_score is None and s is not None:
            best = sci
            best_score = s

    return {
        "bestMatch": best,
        "confidence": {"top1_score": best_score, "level": "species"},
        "topMatches": top_matches[:3],
        "meta": {"project": project, "organs_sent": organs_sent},
    }


def _compact_disease_response(raw: Dict[str, Any]) -> Dict[str, Any]:
    results = raw.get("results") or []
    top_matches: List[Dict[str, Any]] = []

    best = "ismeretlen"
    best_score: Optional[float] = None

    for r in results[:5]:
        score = r.get("score")
        disease = (r.get("disease") or {})
        pest = (r.get("pest") or {})

        name = (
            disease.get("code")
            or disease.get("name")
            or pest.get("code")
            or pest.get("name")
            or r.get("name")
            or r.get("code")
        )
        if not name:
            continue

        s = float(score) if score is not None else None
        top_matches.append({"name": str(name), "score": s})

        if best_score is None and s is not None:
            best = str(name)
            best_score = s

    return {
        "bestMatch": best,
        "confidence": {"top1_score": best_score, "level": "disease_or_pest"},
        "topMatches": top_matches[:3],
        "meta": {},
    }


# ----------------------------
# Sync HTTP helpers (run in thread)
# ----------------------------
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


# ----------------------------
# PlantNet calls (sync in thread)
# ----------------------------
async def _plantnet_identify(
    images: List[Tuple[str, bytes, str]],
    *,
    project: str,
    organs: Optional[str],  # None => Auto (nem küldjük)
) -> Tuple[Dict[str, Any], Optional[str]]:
    _require_api_key()
    if not images:
        raise HTTPException(status_code=422, detail="Nincs kép az azonosításhoz.")

    url = f"{PLANTNET_BASE_URL}/v2/identify/{project}"
    files = [("images", (fn or "image.jpg", b, mt or "image/jpeg")) for (fn, b, mt) in images]

    # ✅ Auto organs: ha nincs megadva, nem küldjük PlantNetnek (app Auto viselkedés)
    data = None
    organs_sent: Optional[str] = None
    if organs is not None and str(organs).strip() != "":
        organs_list = _normalize_organs_for_images(str(organs), len(images))
        data = {"organs": organs_list}  # httpx ismételt multipart fieldként kezeli
        organs_sent = str(organs).strip().lower()

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

    return r.json(), organs_sent


async def _plantnet_diseases_identify(image: Tuple[str, bytes, str]) -> Dict[str, Any]:
    _require_api_key()

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


# ----------------------------
# Routes
# ----------------------------
@app.get("/")
async def root() -> Dict[str, Any]:
    return {"status": "ok"}


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok"}


@app.get("/version")
async def version() -> Dict[str, Any]:
    return {"version": APP_VERSION, "python": platform.python_version(), "httpx": httpx.__version__}


@app.post("/diagnose_files")
async def diagnose_files(payload: Dict[str, Any] = Body(...)):
    refs = payload.get("openaiFileIdRefs") or []
    if not isinstance(refs, list) or len(refs) < 1:
        raise HTTPException(status_code=422, detail="Hiányzik: openaiFileIdRefs (min 1 kép).")
    if len(refs) > 5:
        raise HTTPException(status_code=422, detail="Túl sok kép. Max 5 kép / eset.")

    # ✅ project: alapból env (weurope), de adhatsz override-ot, ha akarod
    project = (payload.get("project") or "").strip() or PLANTNET_PROJECT

    # ✅ organs: Auto ha nincs megadva (None), különben elküldjük
    organs = payload.get("organs", None)
    if isinstance(organs, str) and organs.strip() == "":
        organs = None

    mode = (payload.get("mode") or "learning").strip().lower()
    if mode not in ("learning", "expert"):
        mode = "learning"

    case_type = (payload.get("caseType") or "weed").strip().lower()
    if case_type not in ("weed", "disease", "pest"):
        case_type = "weed"

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

    plant_raw, organs_sent = await _plantnet_identify(images, project=project, organs=organs)
    plant = _compact_species_response(plant_raw, project=project, organs_sent=organs_sent)

    disease_raw = await _plantnet_diseases_identify(images[0])
    disease = _compact_disease_response(disease_raw)

    summary = {
        "bestPlant": plant["bestMatch"],
        "plantScore": plant["confidence"]["top1_score"],
        "topPlants": plant["topMatches"],
        "bestIssue": disease["bestMatch"],
        "issueScore": disease["confidence"]["top1_score"],
        "topIssues": disease["topMatches"],
        "project": project,
        "organsSent": organs_sent,
        "mode": mode,
        "caseType": case_type,
        "imageCount": len(images),
    }

    return {"plant": plant, "diseaseOrPest": disease, "summary": summary}
