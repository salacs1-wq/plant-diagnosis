import os
import re
import base64
import traceback
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, Body, HTTPException, Request
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
app = FastAPI(title="Plant Diagnosis API", version="1.1.6")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------
# GLOBAL ERROR HANDLER
# ----------------------------
@app.exception_handler(Exception)
def global_exception_handler(request: Request, exc: Exception):
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

def _httpx_client_sync() -> httpx.Client:
    timeout = httpx.Timeout(HTTP_TIMEOUT, connect=HTTP_CONNECT_TIMEOUT)
    limits = httpx.Limits(max_keepalive_connections=10, max_connections=20)
    return httpx.Client(
        timeout=timeout,
        limits=limits,
        follow_redirects=True,
        headers={"User-Agent": "PlantDiagnosisBot/1.1.6"},
    )

def _download_openai_file_sync(download_link: str) -> Tuple[bytes, str]:
    if not download_link:
        raise HTTPException(status_code=422, detail="Hiányzik: openaiFileIdRefs[0].download_link")

    with _httpx_client_sync() as client:
        try:
            r = client.get(download_link)
        except httpx.TimeoutException as e:
            raise HTTPException(status_code=502, detail={"file_fetch_error": "timeout", "message": str(e)})
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail={"file_fetch_error": "request_error", "message": str(e)})

    if r.status_code >= 400:
        ct = r.headers.get("content-type")
        preview = None
        try:
            preview = r.text[:300] if (ct and ("text" in ct or "json" in ct)) else None
        except Exception:
            preview = None
        raise HTTPException(
            status_code=502,
            detail={"file_fetch_status": r.status_code, "file_fetch_content_type": ct, "file_fetch_preview": preview},
        )

    data = r.content
    if not data or len(data) < 50:
        raise HTTPException(status_code=422, detail="A letöltött kép üres vagy túl kicsi.")

    mime = r.headers.get("content-type") or "image/jpeg"
    return data, mime

def _compact_species_response(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {"bestMatch": "ismeretlen", "confidence": {"top1_score": None, "level": "species"}, "topMatches": []}
    results = raw.get("results") or []
    best = "ismeretlen"
    best_score = None
    top = []
    for r in results[:5]:
        if not isinstance(r, dict):
            continue
        score = r.get("score")
        species = r.get("species") or {}
        sci = species.get("scientificNameWithoutAuthor") or species.get("scientificName") if isinstance(species, dict) else None
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
    }

def _compact_disease_response(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {"bestMatch": "ismeretlen", "confidence": {"top1_score": None, "level": "disease_or_pest"}, "topMatches": []}
    results = raw.get("results") or raw.get("diseases") or []
    best = "ismeretlen"
    best_score = None
    top = []
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
    }

# ----------------------------
# ENDPOINTS
# ----------------------------
@app.get("/")
def root():
    return {"status": "ok"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/_build")
def build():
    return {"version": "1.1.6", "python": os.sys.version, "httpx": httpx.__version__}

@app.post("/diagnose_files")
def diagnose_files(payload: Dict[str, Any] = Body(...)):
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

    img_bytes, mime = _download_openai_file_sync(first.get("download_link"))

    # --- 1) Plant identify: multipart ONLY via files list (NO data=)
    identify_url = f"{PLANTNET_BASE_URL}/v2/identify/{project}"
    identify_params = [("api-key", PLANTNET_API_KEY)]

    identify_files = []
    identify_files.append(("images", ("image.jpg", img_bytes, mime)))
    for o in organs_list:
        identify_files.append(("organs", (None, o)))  # form field repeated

    with _httpx_client_sync() as client:
        try:
            r1 = client.post(identify_url, params=identify_params, files=identify_files)
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail={"plantnet_stage": "identify", "network_error": str(e)})

    if r1.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"plantnet_stage": "identify", "plantnet_status": r1.status_code, "plantnet_error": _safe_json(r1)},
        )

    # --- 2) Disease identify
    disease_url = f"{PLANTNET_BASE_URL}/v2/diseases/identify"
    
    # PlantNet diseases: NINCS project query param
disease_params = [("api-key", PLANTNET_API_KEY)]

# opcionális: organs itt is küldhető form fieldként (1 kép -> 1 organ)
disease_files = [("images", ("image.jpg", img_bytes, mime))]
for o in organs_list[:1]:
    disease_files.append(("organs", (None, o)))

    with _httpx_client_sync() as client:
        try:
            r2 = client.post(disease_url, params=disease_params, files=disease_files)
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail={"plantnet_stage": "diseases", "network_error": str(e)})

    if r2.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"plantnet_stage": "diseases", "plantnet_status": r2.status_code, "plantnet_error": _safe_json(r2)},
        )

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
