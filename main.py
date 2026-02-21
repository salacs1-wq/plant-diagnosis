import os
import re
import base64
from typing import Any, Dict, List, Optional, Tuple
import traceback

import httpx
from fastapi import FastAPI, File, UploadFile, Query, Body, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# ----------------------------
# CONFIG
# ----------------------------
PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_PROJECT = os.getenv("PLANTNET_PROJECT", "weurope").strip()
PLANTNET_BASE_URL = os.getenv("PLANTNET_BASE_URL", "https://my-api.plantnet.org").rstrip("/")

HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "30"))
HTTP_CONNECT_TIMEOUT = float(os.getenv("HTTP_CONNECT_TIMEOUT", "10"))

DATA_URL_RE = re.compile(
    r"^data:(?P<mime>[^;]+);base64,(?P<data>.+)$",
    re.IGNORECASE | re.DOTALL,
)

# ----------------------------
# APP
# ----------------------------
app = FastAPI(title="Plant Diagnosis API", version="1.1.4")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------
# GLOBAL ERROR HANDLER (ne legyen néma 500)
# ----------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "error": "internal_server_error",
                "type": exc.__class__.__name__,
                "message": str(exc),
                "path": request.url.path,
                "trace": traceback.format_exc().splitlines()[-12:],
            }
        },
    )

# ----------------------------
# HTTPX FACTORY (PER-REQUEST)
# ----------------------------
def _httpx_client() -> httpx.AsyncClient:
    timeout = httpx.Timeout(HTTP_TIMEOUT, connect=HTTP_CONNECT_TIMEOUT)
    limits = httpx.Limits(max_keepalive_connections=10, max_connections=20)
    return httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        follow_redirects=True,
        headers={"User-Agent": "PlantDiagnosisBot/1.1.4"},
    )

# ----------------------------
# HELPERS
# ----------------------------
def _require_api_key():
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="PLANTNET_API_KEY hiányzik (Render Environment)")

def _normalize_organs(organs: Optional[str]) -> List[str]:
    if not organs:
        return ["leaf"]
    parts = re.split(r"[,\s]+", str(organs).strip())
    parts = [p for p in parts if p]
    return parts or ["leaf"]

def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        try:
            return {"status": resp.status_code, "text": resp.text[:500]}
        except Exception:
            return {"status": resp.status_code, "non_json": True}

def _plantnet_params_only_key() -> List[Tuple[str, str]]:
    # PlantNet: api-key query param
    return [("api-key", PLANTNET_API_KEY)]

def _plantnet_organs_form(organs_list: List[str]) -> List[Tuple[str, str]]:
    # PlantNet v2 identify: organs mező form-data-ban, többször ismételve
    return [("organs", o) for o in (organs_list or ["leaf"])]

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
            "meta": {"note": "PlantNet válasz nem dict"},
        }

    results = raw.get("results") or []
    top = []
    best = "ismeretlen"
    best_score = None

    for r in results[:5]:
        if not isinstance(r, dict):
            continue
        score = r.get("score")
        species = r.get("species") or {}
        sci = None
        if isinstance(species, dict):
            sci = species.get("scientificNameWithoutAuthor") or species.get("scientificName")
        if not sci:
            continue
        if best == "ismeretlen":
            best = sci
            best_score = score
        top.append({"name": sci, "score": float(score) if score is not None else None})

    return {
        "bestMatch": best,
        "confidence": {"top1_score": float(best_score) if best_score is not None else None, "level": "species"},
        "topMatches": top[:3],
        "meta": {"project": (raw.get("query") or {}).get("project") or PLANTNET_PROJECT},
    }

def _compact_disease_response(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {
            "bestMatch": "ismeretlen",
            "confidence": {"top1_score": None, "level": "disease_or_pest"},
            "topMatches": [],
            "meta": {"note": "PlantNet válasz nem dict"},
        }

    results = raw.get("results") or raw.get("diseases") or []
    top = []
    best = "ismeretlen"
    best_score = None

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
        if best == "ismeretlen":
            best = name
            best_score = score
        top.append({"name": name, "score": float(score) if score is not None else None})

    return {
        "bestMatch": best,
        "confidence": {"top1_score": float(best_score) if best_score is not None else None, "level": "disease_or_pest"},
        "topMatches": top[:3],
        "meta": {"project": (raw.get("query") or {}).get("project") or PLANTNET_PROJECT},
    }

async def _download_openai_file(download_link: str) -> Tuple[bytes, str]:
    if not download_link:
        raise HTTPException(status_code=422, detail="Hiányzik: openaiFileIdRefs[0].download_link")

    async with _httpx_client() as client:
        try:
            r = await client.get(download_link)
        except httpx.TimeoutException as e:
            raise HTTPException(status_code=502, detail={"file_fetch_error": "timeout", "message": str(e)})
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail={"file_fetch_error": "request_error", "message": str(e)})

    if r.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={
                "file_fetch_status": r.status_code,
                "file_fetch_content_type": r.headers.get("content-type"),
                "file_fetch_preview": (r.text[:300] if "text" in (r.headers.get("content-type") or "") else None),
            },
        )

    data = r.content
    if not data or len(data) < 50:
        raise HTTPException(status_code=422, detail="A letöltött kép üres vagy túl kicsi.")

    mime = r.headers.get("content-type") or "image/jpeg"
    return data, mime

# ----------------------------
# ENDPOINTS
# ----------------------------
@app.get("/")
async def root():
    return {"status": "ok"}

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/_build")
async def build():
    return {"version": "1.1.4", "python": os.sys.version, "httpx": httpx.__version__}

# --------- GPT Actions main endpoint ----------
@app.post("/diagnose_files")
async def diagnose_files(payload: Dict[str, Any] = Body(...)):
    _require_api_key()

    organs = payload.get("organs") or "leaf"
    project = payload.get("project") or PLANTNET_PROJECT
    organs_list = _normalize_organs(organs)

    refs = payload.get("openaiFileIdRefs")
    if not refs or not isinstance(refs, list):
        raise HTTPException(status_code=422, detail="Hiányzik: openaiFileIdRefs (lista)")

    first = refs[0]
    if not isinstance(first, dict):
        raise HTTPException(status_code=422, detail="Hibás openaiFileIdRefs[0] (objektumot várunk)")

    img_bytes, mime = await _download_openai_file(first.get("download_link"))

    # 1) Plant identify
    identify_url = f"{PLANTNET_BASE_URL}/v2/identify/{project}"
    identify_params = _plantnet_params_only_key()
    identify_data = _plantnet_organs_form(organs_list)
    identify_files = {"images": ("image.jpg", img_bytes, mime)}

    async with _httpx_client() as client:
        try:
            r1 = await client.post(identify_url, params=identify_params, data=identify_data, files=identify_files)
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail={"plantnet_stage": "identify", "network_error": str(e)})

    if r1.status_code >= 400:
        raise HTTPException(status_code=502, detail={"plantnet_stage": "identify", "plantnet_status": r1.status_code, "plantnet_error": _safe_json(r1)})

    # 2) Disease identify
    disease_url = f"{PLANTNET_BASE_URL}/v2/diseases/identify"
    disease_params = [("api-key", PLANTNET_API_KEY), ("project", str(project))]
    disease_files = {"images": ("image.jpg", img_bytes, mime)}

    async with _httpx_client() as client:
        try:
            r2 = await client.post(disease_url, params=disease_params, files=disease_files)
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail={"plantnet_stage": "diseases", "network_error": str(e)})

    if r2.status_code >= 400:
        raise HTTPException(status_code=502, detail={"plantnet_stage": "diseases", "plantnet_status": r2.status_code, "plantnet_error": _safe_json(r2)})

    # Compact (GPT-friendly)
    plant_compact = _compact_species_response(_safe_json(r1))
    disease_compact = _compact_disease_response(_safe_json(r2))

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

# --------- Optional: base64 endpoints (hasznos külső integrációra) ----------
@app.post("/identify_b64")
async def identify_b64(payload: Dict[str, Any] = Body(...)):
    _require_api_key()
    img_bytes, mime = _decode_base64_image(payload.get("image_base64") or "")
    organs_list = _normalize_organs(payload.get("organs") or "leaf")
    project = payload.get("project") or PLANTNET_PROJECT

    url = f"{PLANTNET_BASE_URL}/v2/identify/{project}"
    params = _plantnet_params_only_key()
    data = _plantnet_organs_form(organs_list)
    files = {"images": ("image.jpg", img_bytes, mime)}

    async with _httpx_client() as client:
        r = await client.post(url, params=params, data=data, files=files)

    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail={"plantnet_stage": "identify", "plantnet_status": r.status_code, "plantnet_error": _safe_json(r)})

    return _compact_species_response(_safe_json(r))

@app.post("/diseases/identify_b64")
async def disease_b64(payload: Dict[str, Any] = Body(...)):
    _require_api_key()
    img_bytes, mime = _decode_base64_image(payload.get("image_base64") or "")
    project = payload.get("project") or PLANTNET_PROJECT

    url = f"{PLANTNET_BASE_URL}/v2/diseases/identify"
    params = [("api-key", PLANTNET_API_KEY), ("project", str(project))]
    files = {"images": ("image.jpg", img_bytes, mime)}

    async with _httpx_client() as client:
        r = await client.post(url, params=params, files=files)

    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail={"plantnet_stage": "diseases", "plantnet_status": r.status_code, "plantnet_error": _safe_json(r)})

    return _compact_disease_response(_safe_json(r))
