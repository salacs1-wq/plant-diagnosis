import os
import re
import base64
import traceback
from typing import Any, Dict, List, Optional, Tuple
from contextlib import asynccontextmanager

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

DEFAULT_TIMEOUT = 30.0
DEFAULT_CONNECT_TIMEOUT = 10.0

DATA_URL_RE = re.compile(
    r"^data:(?P<mime>[^;]+);base64,(?P<data>.+)$",
    re.IGNORECASE | re.DOTALL,
)

# ----------------------------
# LIFESPAN – ASYNC CLIENT
# ----------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    timeout = httpx.Timeout(DEFAULT_TIMEOUT, connect=DEFAULT_CONNECT_TIMEOUT)
    limits = httpx.Limits(max_keepalive_connections=20, max_connections=50)

    app.state.http = httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        follow_redirects=True,
        headers={"User-Agent": "PlantDiagnosisBot/1.1.3"},
    )

    try:
        yield
    finally:
        await app.state.http.aclose()

app = FastAPI(
    title="Plant Diagnosis API",
    version="1.1.3",
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
# GLOBAL ERROR HANDLER
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
                "trace": traceback.format_exc().splitlines()[-10:],
            }
        },
    )

# ----------------------------
# UTIL
# ----------------------------
def _client() -> httpx.AsyncClient:
    return app.state.http

def _require_api_key():
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="PLANTNET_API_KEY hiányzik")

def _normalize_organs(organs: Optional[str]) -> List[str]:
    if not organs:
        return ["leaf"]
    parts = re.split(r"[,\s]+", organs.strip())
    return [p for p in parts if p] or ["leaf"]

def _plantnet_params_only_key():
    return [("api-key", PLANTNET_API_KEY)]

def _plantnet_organs_form(organs_list: List[str]):
    return [("organs", o) for o in organs_list]

def _safe_json(resp: httpx.Response):
    try:
        return resp.json()
    except Exception:
        return {"status": resp.status_code, "text": resp.text[:500]}

# ----------------------------
# OPENAI FILE DOWNLOAD
# ----------------------------
async def _download_openai_file(download_link: str) -> Tuple[bytes, str]:
    if not download_link:
        raise HTTPException(status_code=422, detail="download_link hiányzik")

    try:
        response = await _client().request("GET", download_link)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail={"file_fetch_error": str(e)})

    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={
                "file_fetch_status": response.status_code,
                "file_fetch_preview": response.text[:300],
            },
        )

    content = response.content
    if not content or len(content) < 50:
        raise HTTPException(status_code=422, detail="Letöltött kép üres")

    mime = response.headers.get("content-type", "image/jpeg")
    return content, mime

# ----------------------------
# BUILD INFO
# ----------------------------
@app.get("/_build")
async def build_info():
    return {
        "version": "1.1.3",
        "python": os.sys.version,
        "httpx": httpx.__version__,
    }

@app.get("/")
async def root():
    return {"status": "ok"}

@app.get("/health")
async def health():
    return {"status": "ok"}

# ----------------------------
# DIAGNOSE_FILES (GPT Actions)
# ----------------------------
@app.post("/diagnose_files")
async def diagnose_files(payload: Dict[str, Any] = Body(...)):
    _require_api_key()

    organs = payload.get("organs") or "leaf"
    project = payload.get("project") or PLANTNET_PROJECT
    organs_list = _normalize_organs(organs)

    refs = payload.get("openaiFileIdRefs")
    if not refs or not isinstance(refs, list):
        raise HTTPException(status_code=422, detail="openaiFileIdRefs hiányzik")

    first = refs[0]
    download_link = first.get("download_link")

    img_bytes, mime = await _download_openai_file(download_link)

    # --- Plant identify ---
    identify_url = f"{PLANTNET_BASE_URL}/v2/identify/{project}"
    try:
        r1 = await _client().post(
            identify_url,
            params=_plantnet_params_only_key(),
            data=_plantnet_organs_form(organs_list),
            files={"images": ("image.jpg", img_bytes, mime)},
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail={"plantnet_identify_error": str(e)})

    if r1.status_code >= 400:
        raise HTTPException(status_code=502, detail=_safe_json(r1))

    # --- Disease identify ---
    disease_url = f"{PLANTNET_BASE_URL}/v2/diseases/identify"
    try:
        r2 = await _client().post(
            disease_url,
            params=[("api-key", PLANTNET_API_KEY), ("project", project)],
            files={"images": ("image.jpg", img_bytes, mime)},
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail={"plantnet_disease_error": str(e)})

    if r2.status_code >= 400:
        raise HTTPException(status_code=502, detail=_safe_json(r2))

    return {
        "plant": r1.json(),
        "diseaseOrPest": r2.json(),
    }
