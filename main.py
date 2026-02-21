import os
import re
import base64
from typing import Any, Dict, List, Optional, Tuple
from contextlib import asynccontextmanager
import traceback

import httpx
from fastapi import FastAPI, File, UploadFile, Query, Body, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# ----------------------------
# Config
# ----------------------------
PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_PROJECT = os.getenv("PLANTNET_PROJECT", "weurope").strip()
PLANTNET_BASE_URL = os.getenv("PLANTNET_BASE_URL", "https://my-api.plantnet.org").rstrip("/")

DEFAULT_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "30"))
DEFAULT_CONNECT_TIMEOUT = float(os.getenv("HTTP_CONNECT_TIMEOUT", "10"))

DATA_URL_RE = re.compile(
    r"^data:(?P<mime>[^;]+);base64,(?P<data>.+)$",
    re.IGNORECASE | re.DOTALL,
)

# ----------------------------
# Lifespan (reuse HTTP client)
# ----------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    timeout = httpx.Timeout(DEFAULT_TIMEOUT, connect=DEFAULT_CONNECT_TIMEOUT)
    headers = {"User-Agent": "Mozilla/5.0 (PlantDiagnosisBot/1.1)"}
    limits = httpx.Limits(max_keepalive_connections=20, max_connections=50)

    app.state.http = httpx.AsyncClient(
        timeout=timeout,
        headers=headers,
        limits=limits,
        follow_redirects=True,
    )
    try:
        yield
    finally:
        await app.state.http.aclose()

app = FastAPI(
    title="Növénydiagnosztikai API",
    version="1.1.2",
    description="PlantNet proxy (növényazonosítás + betegség/kártevő azonosítás) GPT-hez optimalizált válaszokkal.",
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
# Global error handler (hogy a 500 ne legyen vak)
# ----------------------------
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Ne dobjunk el információt: Actions debughoz kell.
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "error": "internal_server_error",
                "type": exc.__class__.__name__,
                "message": str(exc),
                "path": str(request.url.path),
                "trace": traceback.format_exc().splitlines()[-12:],  # rövidített stack
            }
        },
    )

# ----------------------------
# Helpers
# ----------------------------
def _client(app: FastAPI) -> httpx.AsyncClient:
    return app.state.http

def _require_api_key() -> None:
    if not PLANTNET_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Hiányzik a PLANTNET_API_KEY környezeti változó (Render -> Environment).",
        )

def _normalize_organs(organs: Optional[str]) -> List[str]:
    if not organs:
        return ["leaf"]
    parts = re.split(r"[,\s]+", organs.strip())
    parts = [p for p in parts if p]
    return parts or ["leaf"]

def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        txt = None
        try:
            # ne törjön el bináris tartalmon
            txt = resp.text[:500]
        except Exception:
            pass
        return {"non_json": True, "status_code": resp.status_code, "text_preview": txt}

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

def _compact_species_response(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {
            "bestMatch": "ismeretlen",
            "confidence": {"top1_score": None, "level": "species"},
            "topMatches": [],
            "meta": {"note": "PlantNet válasz nem dict", "raw_type": type(raw).__name__},
        }

    results = raw.get("results") or []
    top_matches: List[Dict[str, Any]] = []
    best = None
    best_score = None

    for r in results[:5]:
        if not isinstance(r, dict):
            continue
        score = r.get("score")
        species = (r.get("species") or {})
        sci = None
        if isinstance(species, dict):
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

    return {
        "bestMatch": best or "ismeretlen",
        "confidence": {
            "top1_score": float(best_score) if best_score is not None else None,
            "level": "species",
        },
        "topMatches": top_matches[:3],
        "meta": {
            "project": (raw.get("query") or {}).get("project") or PLANTNET_PROJECT,
        },
    }

def _compact_disease_response(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {
            "bestMatch": "ismeretlen",
            "confidence": {"top1_score": None, "level": "disease_or_pest"},
            "topMatches": [],
            "meta": {"note": "PlantNet válasz nem dict", "raw_type": type(raw).__name__},
        }

    results = raw.get("results") or raw.get("diseases") or []
    candidates: List[Dict[str, Any]] = []
    top_name = None
    top_score = None

    for r in results[:5]:
        if not isinstance(r, dict):
            continue
        score = r.get("score") or r.get("confidence")
        name = r.get("name")
        if not name:
            d = r.get("disease") if isinstance(r.get("disease"), dict) else None
            if d:
                name = d.get("name") or d.get("id")
        if not name:
            continue
        if top_name is None:
            top_name = name
            top_score = score
        candidates.append({"name": name, "score": float(score) if score is not None else None})

    return {
        "bestMatch": top_name or "ismeretlen",
        "confidence": {
            "top1_score": float(top_score) if top_score is not None else None,
            "level": "disease_or_pest",
        },
        "topMatches": candidates[:3],
        "meta": {"project": (raw.get("query") or {}).get("project") or PLANTNET_PROJECT},
    }

# --- PlantNet request building ---
def _plantnet_params_only_key() -> List[Tuple[str, str]]:
    return [("api-key", PLANTNET_API_KEY)]

def _plantnet_organs_form(organs_list: List[str]) -> List[Tuple[str, str]]:
    ol = organs_list or ["leaf"]
    return [("organs", o) for o in ol]

# --- OpenAI file download (Actions) ---
async def _download_openai_file(download_link: str) -> Tuple[bytes, str]:
    if not download_link:
        raise HTTPException(status_code=422, detail="Hiányzik: openaiFileIdRefs.download_link")

    try:
        r = await _client(app).get(download_link)
    except httpx.TimeoutException as e:
        raise HTTPException(status_code=502, detail={"file_fetch_error": "timeout", "message": str(e)})
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail={"file_fetch_error": "request_error", "message": str(e)})

    if r.status_code >= 400:
        # ne használjunk r.text-et vakon
        preview = None
        ct = r.headers.get("content-type", "")
        try:
            if "text" in ct or "json" in ct:
                preview = r.text[:500]
            else:
                preview = (r.content[:200]).hex()
        except Exception:
            preview = None

        raise HTTPException(
            status_code=502,
            detail={
                "file_fetch_status": r.status_code,
                "file_fetch_content_type": ct,
                "file_fetch_preview": preview,
            },
        )

    data = r.content
    if not data or len(data) < 50:
        raise HTTPException(status_code=422, detail="A letöltött kép üres vagy túl kicsi.")

    mime = r.headers.get("content-type") or "image/jpeg"
    return data, mime

# ----------------------------
# Endpoints
# ----------------------------
@app.get("/")
async def rootGet():
    return {"status": "ok"}

@app.get("/health")
async def healthGet():
    return {"status": "ok"}

@app.post("/diagnose_files")
async def diagnoseFiles(payload: Dict[str, Any] = Body(...)):
    """
    GPT Actions kompatibilis diagnózis:
    - openaiFileIdRefs: [{name,id,mime_type,download_link}, ...]
    - organs, project opcionális
    """
    _require_api_key()

    organs = payload.get("organs") or "leaf"
    project = payload.get("project") or PLANTNET_PROJECT
    organs_list = _normalize_organs(str(organs))

    refs = payload.get("openaiFileIdRefs") or []
    if not refs or not isinstance(refs, list):
        raise HTTPException(status_code=422, detail="Hiányzik vagy hibás: openaiFileIdRefs (lista)")

    first = refs[0]
    if not isinstance(first, dict):
        raise HTTPException(status_code=422, detail="Hibás openaiFileIdRefs elem (objektumot várunk).")

    download_link = first.get("download_link")
    img_bytes, mime = await _download_openai_file(download_link)

    # 1) Plant identify
    identify_url = f"{PLANTNET_BASE_URL}/v2/identify/{project}"
    identify_params = _plantnet_params_only_key()
    identify_data = _plantnet_organs_form(organs_list)
    identify_files = {"images": ("image.jpg", img_bytes, mime)}

    try:
        r1 = await _client(app).post(identify_url, params=identify_params, data=identify_data, files=identify_files)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail={"plantnet_stage": "identify", "network_error": str(e)})

    if r1.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"plantnet_stage": "identify", "plantnet_status": r1.status_code, "plantnet_error": _safe_json(r1)},
        )

    # 2) Disease identify
    disease_url = f"{PLANTNET_BASE_URL}/v2/diseases/identify"
    disease_params = [("api-key", PLANTNET_API_KEY), ("project", str(project))]
    disease_files = {"images": ("image.jpg", img_bytes, mime)}

    try:
        r2 = await _client(app).post(disease_url, params=disease_params, files=disease_files)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail={"plantnet_stage": "diseases", "network_error": str(e)})

    if r2.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"plantnet_stage": "diseases", "plantnet_status": r2.status_code, "plantnet_error": _safe_json(r2)},
        )

    # JSON parse safety
    try:
        plant_raw = r1.json()
    except Exception:
        plant_raw = {"non_json": True, "status": r1.status_code, "text": (r1.text[:500] if hasattr(r1, "text") else None)}

    try:
        disease_raw = r2.json()
    except Exception:
        disease_raw = {"non_json": True, "status": r2.status_code, "text": (r2.text[:500] if hasattr(r2, "text") else None)}

    plant_compact = _compact_species_response(plant_raw)
    disease_compact = _compact_disease_response(disease_raw)

    return {
        "plant": plant_compact,
        "diseaseOrPest": disease_compact,
        "summary": {
            "bestPlant": plant_compact.get("bestMatch"),
            "plantScore": (plant_compact.get("confidence") or {}).get("top1_score"),
            "bestIssue": disease_compact.get("bestMatch"),
            "issueScore": (disease_compact.get("confidence") or {}).get("top1_score"),
            "project": project,
            "organs": organs_list,
        },
    }
