import os
import re
import base64
import platform
import traceback
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, File, UploadFile, Query, Body, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager


# ----------------------------
# Config
# ----------------------------
APP_VERSION = os.getenv("APP_VERSION", "1.1.8").strip()

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()

# default: all (app konzisztencia)
PLANTNET_PROJECT = os.getenv("PLANTNET_PROJECT", "all").strip()

PLANTNET_BASE_URL = os.getenv("PLANTNET_BASE_URL", "https://my-api.plantnet.org").rstrip("/")

DEFAULT_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "45"))
DEFAULT_CONNECT_TIMEOUT = float(os.getenv("HTTP_CONNECT_TIMEOUT", "15"))

DATA_URL_RE = re.compile(
    r"^data:(?P<mime>[^;]+);base64,(?P<data>.+)$",
    re.IGNORECASE | re.DOTALL,
)


# ----------------------------
# Lifespan: shared AsyncClient
# ----------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    timeout = httpx.Timeout(DEFAULT_TIMEOUT, connect=DEFAULT_CONNECT_TIMEOUT)
    app.state.http = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
    try:
        yield
    finally:
        await app.state.http.aclose()


app = FastAPI(
    title="Növénydiagnosztikai API (PlantNet proxy)",
    version=APP_VERSION,
    description="PlantNet proxy (identify + diseases) GPT-hez optimalizált válaszokkal.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------------
# Global error handler (ne legyen néma 500)
# ----------------------------
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Ha már HTTPException, azt FastAPI kezeli
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
                "trace": traceback.format_exc().splitlines()[-20:],  # utolsó 20 sor
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


def _normalize_organs_for_images(organs: Optional[str], image_count: int) -> List[str]:
    organ = (organs or "").strip().lower() or "leaf"
    return [organ] * max(1, image_count)


def _decode_base64_image(image_base64: str) -> Tuple[bytes, str]:
    image_base64 = (image_base64 or "").strip()
    if not image_base64:
        raise HTTPException(status_code=422, detail="Hiányzik: image_base64")

    mime = "image/jpeg"
    m = DATA_URL_RE.match(image_base64)
    if m:
        mime = m.group("mime").strip().lower()
        image_base64 = m.group("data").strip()

    image_base64 = re.sub(r"\s+", "", image_base64)

    missing = len(image_base64) % 4
    if missing:
        image_base64 += "=" * (4 - missing)

    try:
        data = base64.b64decode(image_base64, validate=False)
    except Exception:
        raise HTTPException(status_code=422, detail="Nem érvényes base64 (dekódolási hiba).")

    if not data or len(data) < 50:
        raise HTTPException(status_code=422, detail="A kép túl kicsi vagy üres (base64).")

    return data, mime


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
        "topMatches": top_matches[:3],
        "meta": {"project": project, "organs_sent": organs_sent},
    }


def _compact_disease_response(raw: Dict[str, Any]) -> Dict[str, Any]:
    results = raw.get("results") or []
    top_matches: List[Dict[str, Any]] = []
    best = None
    best_score = None

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
        if best is None:
            best = str(name)
            best_score = score
        top_matches.append({"name": str(name), "score": float(score) if score is not None else None})

    top1 = float(best_score) if best_score is not None else None
    return {
        "bestMatch": best or "ismeretlen",
        "confidence": {"top1_score": top1, "level": "disease_or_pest"},
        "topMatches": top_matches[:3],
        "meta": {},
    }


async def _download_bytes(url: str) -> bytes:
    if not url or not isinstance(url, str):
        raise HTTPException(status_code=422, detail="Hiányzik: download_link")
    client: httpx.AsyncClient = app.state.http
    r = await client.get(url)
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail={"stage": "download", "status": r.status_code, "text": r.text[:500]})
    data = r.content
    if not data or len(data) < 50:
        raise HTTPException(status_code=422, detail="A letöltött kép üres vagy túl kicsi.")
    return data


# ----------------------------
# PlantNet calls
# ----------------------------
async def _plantnet_identify(
    images: List[Tuple[str, bytes, str]],
    *,
    project: str,
    organs: Optional[str],
) -> Dict[str, Any]:
    """
    /v2/identify/{project}
    api-key query param
    organs: multipart FORM mezőként, ismételve képenként
    """
    _require_api_key()
    if not images:
        raise HTTPException(status_code=422, detail="Nincs kép az azonosításhoz.")

    identify_url = f"{PLANTNET_BASE_URL}/v2/identify/{project}"
    organs_list = _normalize_organs_for_images(organs, len(images))

    files = [("images", (fn or "image.jpg", b, mt or "image/jpeg")) for (fn, b, mt) in images]
    data = [("organs", o) for o in organs_list]
    params = {"api-key": PLANTNET_API_KEY}

    client: httpx.AsyncClient = app.state.http
    r = await client.post(identify_url, params=params, data=data, files=files)

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
    """
    /v2/diseases/identify
    Csak api-key query param, NINCS project, NINCS organs.
    """
    _require_api_key()
    url = f"{PLANTNET_BASE_URL}/v2/diseases/identify"
    params = {"api-key": PLANTNET_API_KEY}
    files = {"images": (image[0] or "image.jpg", image[1], image[2] or "image/jpeg")}

    client: httpx.AsyncClient = app.state.http
    r = await client.post(url, params=params, files=files)

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

    organs = (payload.get("organs") or "leaf").strip().lower() or "leaf"
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

    project = PLANTNET_PROJECT  # default: all

    plant_raw = await _plantnet_identify(images, project=project, organs=organs)
    plant = _compact_species_response(plant_raw, project=project, organs_sent=organs)

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
        "organsSent": organs,
        "mode": mode,
        "caseType": case_type,
        "imageCount": len(images),
    }

    return {"plant": plant, "diseaseOrPest": disease, "summary": summary}
