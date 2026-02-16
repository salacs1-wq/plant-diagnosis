# main.py
import os
import uuid
import mimetypes
from pathlib import Path
from typing import Optional, Literal, Dict, Any, List

import requests
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl

# ----------------------------
# Config
# ----------------------------
PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_PROJECT = os.getenv("PLANTNET_PROJECT", "all").strip()  # "all" is ok
PLANTNET_BASE = os.getenv("PLANTNET_BASE", "https://my-api.plantnet.org/v2/identify").rstrip("/")

# Render: use a writable dir. /tmp is always writable, but ephemeral.
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/tmp/uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}

# ----------------------------
# App
# ----------------------------
app = FastAPI(title="Plant Diagnosis API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later if you want
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve uploaded files publicly at /files/<name>
app.mount("/files", StaticFiles(directory=str(UPLOAD_DIR), html=False), name="files")


# ----------------------------
# Helpers
# ----------------------------
def _require_key():
    if not PLANTNET_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Missing PLANTNET_API_KEY env var on the server."
        )

def _ext_from_upload(upload: UploadFile) -> str:
    # Try filename ext
    if upload.filename:
        ext = Path(upload.filename).suffix.lower()
        if ext in ALLOWED_EXT:
            return ext
    # Fallback from content type
    if upload.content_type:
        guess = mimetypes.guess_extension(upload.content_type)
        if guess and guess.lower() in ALLOWED_EXT:
            return guess.lower()
    # Default
    return ".jpg"

def _public_base_url(request_headers: Dict[str, str]) -> str:
    """
    On Render, requests come through https. We build base from forwarded headers.
    """
    proto = request_headers.get("x-forwarded-proto", "https")
    host = request_headers.get("x-forwarded-host") or request_headers.get("host")
    if not host:
        # Fallback: Render service URL if you set it
        fallback = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
        if fallback:
            return fallback
        raise HTTPException(status_code=500, detail="Cannot determine public base URL (missing host headers).")
    return f"{proto}://{host}"

def _plantnet_identify_from_bytes(image_bytes: bytes, filename: str, organs: str = "leaf") -> Dict[str, Any]:
    _require_key()
    url = f"{PLANTNET_BASE}/{PLANTNET_PROJECT}"
    params = {"api-key": PLANTNET_API_KEY}
    files = {"images": (filename, image_bytes)}
    data = {"organs": organs}

    try:
        r = requests.post(url, params=params, files=files, data=data, timeout=60)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"PlantNet upstream error: {e}")

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)

    return r.json()

def _normalize_plantnet(raw: Dict[str, Any]) -> Dict[str, Any]:
    # PlantNet returns "results": [{score, species:{scientificName,...}}]
    results = raw.get("results") or []
    top = results[0] if results else None

    def pack(item: Dict[str, Any]) -> Dict[str, Any]:
        sp = (item.get("species") or {})
        return {
            "score": item.get("score"),
            "scientificName": sp.get("scientificName"),
            "scientificNameWithoutAuthor": sp.get("scientificNameWithoutAuthor"),
            "family": (sp.get("family") or {}).get("scientificName"),
            "commonNames": sp.get("commonNames") or [],
        }

    top_matches = [pack(x) for x in results[:5]]

    best_match = None
    best_score = None
    if top:
        best_match = (top.get("species") or {}).get("scientificName")
        best_score = top.get("score")

    # very simple confidence level
    level = "alacsony"
    if isinstance(best_score, (int, float)):
        if best_score >= 0.7:
            level = "magas"
        elif best_score >= 0.45:
            level = "kozepes"

    return {
        "bestMatch": best_match,
        "confidence": {
            "top1_score": best_score,
            "level": level
        },
        "topMatches": top_matches,
        "raw": raw,  # keep full response for debugging
    }


# ----------------------------
# Schemas
# ----------------------------
class IdentifyByUrlIn(BaseModel):
    image_url: HttpUrl
    organs: Optional[Literal["leaf", "flower", "fruit", "bark"]] = "leaf"


# ----------------------------
# Routes
# ----------------------------
@app.get("/")
def health():
    return {"status": "ok", "message": "Plant Diagnosis API running"}

@app.post("/upload")
async def upload_image(image: UploadFile = File(...)):
    """
    Upload a file and get a PUBLIC URL back.
    This is the missing piece for GPT Actions: it cannot send /mnt/data paths to PlantNet,
    so we host the file at /files/<name>.
    """
    if not image:
        raise HTTPException(status_code=400, detail="Missing file field: image")

    ext = _ext_from_upload(image)
    name = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_DIR / name

    content = await image.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file.")

    dest.write_bytes(content)

    base = _public_base_url({k.lower(): v for k, v in image.headers.items()})  # headers on UploadFile may be limited
    # Better: use environment if set
    env_base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    if env_base:
        base = env_base

    url = f"{base}/files/{name}"
    return {"url": url, "filename": name, "contentType": image.content_type}

@app.post("/identify")
async def identify_from_upload(
    image: UploadFile = File(...),
    organs: str = Form("leaf"),
):
    """
    Direct identify from uploaded file (works from Hoppscotch/Postman).
    GPT Actions *may* fail to pass binary properly depending on schema/UI,
    so /upload + /identify_url is the reliable path.
    """
    if not image:
        raise HTTPException(status_code=400, detail="Missing file field: image")

    img_bytes = await image.read()
    if not img_bytes:
        raise HTTPException(status_code=400, detail="Empty file.")

    raw = _plantnet_identify_from_bytes(img_bytes, image.filename or "image.jpg", organs=organs)
    return _normalize_plantnet(raw)

@app.post("/identify_url")
def identify_by_url(payload: IdentifyByUrlIn):
    """
    Identify by PUBLIC URL:
    1) GPT calls /upload -> gets https://.../files/<id>.jpg
    2) GPT calls /identify_url with that URL
    """
    _require_key()
    try:
        img = requests.get(str(payload.image_url), timeout=30)
        img.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=400, detail=f"Could not fetch image_url: {e}")

    filename = Path(str(payload.image_url)).name or "image.jpg"
    raw = _plantnet_identify_from_bytes(img.content, filename, organs=payload.organs or "leaf")
    return _normalize_plantnet(raw)
