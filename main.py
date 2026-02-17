# main.py
import os
import uuid
import mimetypes
from pathlib import Path
from typing import Optional, Literal, Dict, Any

import requests
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl

# ----------------------------
# Config
# ----------------------------
PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_PROJECT = os.getenv("PLANTNET_PROJECT", "all").strip()  # e.g. "all"
PLANTNET_BASE = os.getenv("PLANTNET_BASE", "https://my-api.plantnet.org/v2/identify").rstrip("/")

# Render: /tmp writable
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/tmp/uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}

# Optional: set this in Render env for stable URL building
# Example: https://plant-diagnosis-1.onrender.com
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")

# ----------------------------
# App
# ----------------------------
app = FastAPI(title="Plant Diagnosis API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later if needed
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve uploaded files publicly
app.mount("/files", StaticFiles(directory=str(UPLOAD_DIR), html=False), name="files")


# ----------------------------
# Helpers
# ----------------------------
def _require_key():
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="Missing PLANTNET_API_KEY env var on the server.")


def _ext_from_upload(upload: UploadFile) -> str:
    # Try filename ext first
    if upload.filename:
        ext = Path(upload.filename).suffix.lower()
        if ext in ALLOWED_EXT:
            return ext

    # Fallback from content type
    if upload.content_type:
        guess = mimetypes.guess_extension(upload.content_type)
        if guess and guess.lower() in ALLOWED_EXT:
            return guess.lower()

    return ".jpg"


def _public_base_url_from_request(request: Request) -> str:
    """
    Build base URL behind reverse proxy (Render).
    Prefer PUBLIC_BASE_URL if set.
    """
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL

    headers = {k.lower(): v for k, v in request.headers.items()}
    proto = headers.get("x-forwarded-proto", "https")
    host = headers.get("x-forwarded-host") or headers.get("host")
    if not host:
        raise HTTPException(status_code=500, detail="Cannot determine public base URL (missing host headers).")

    return f"{proto}://{host}"


def _plantnet_identify_from_bytes(image_bytes: bytes, filename: str, organs: str = "leaf") -> Dict[str, Any]:
    _require_key()
    url = f"{PLANTNET_BASE}/{PLANTNET_PROJECT}"
    params = {"api-key": PLANTNET_API_KEY}

    # PlantNet expects "images" as multipart field name
    files = {"images": (filename, image_bytes)}
    data = {"organs": organs}

    try:
        r = requests.post(url, params=params, files=files, data=data, timeout=60)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"PlantNet upstream error: {e}")

    if r.status_code >= 400:
        # keep upstream text for debugging
        raise HTTPException(status_code=r.status_code, detail=r.text)

    return r.json()


def _normalize_plantnet(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    PlantNet returns:
      raw["results"] = [{ "score":..., "species":{...}}]
    """
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

    # simple confidence label
    level = "alacsony"
    if isinstance(best_score, (int, float)):
        if best_score >= 0.70:
            level = "magas"
        elif best_score >= 0.45:
            level = "kozepes"

    return {
        "bestMatch": best_match,
        "confidence": {"top1_score": best_score, "level": level},
        "topMatches": top_matches,
        "raw": raw,  # passthrough for debugging
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
async def upload_image(request: Request, image: UploadFile = File(...)):
    """
    Upload image and return a PUBLIC URL: /files/<uuid>.jpg
    This is the reliable path for GPT Actions.
    """
    if not image:
        raise HTTPException(status_code=400, detail="Missing file field: image")

    content = await image.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file.")

    ext = _ext_from_upload(image)
    name = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_DIR / name
    dest.write_bytes(content)

    base = _public_base_url_from_request(request)
    url = f"{base}/files/{name}"
    return {"url": url, "filename": name, "contentType": image.content_type}


@app.post("/identify")
async def identify_from_upload(
    image: UploadFile = File(...),
    organs: str = Form("leaf"),
):
    """
    Direct identify from uploaded file (works from Hoppscotch/Postman).
    GPT Actions sometimes fails to send binary reliably, use /upload + /identify_url.
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
      1) call /upload -> get https://.../files/<id>.jpg
      2) call /identify_url with that URL
    """
    _require_key()

    try:
        resp = requests.get(str(payload.image_url), timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=400, detail=f"Could not fetch image_url: {e}")

    filename = Path(str(payload.image_url)).name or "image.jpg"
    raw = _plantnet_identify_from_bytes(resp.content, filename, organs=payload.organs or "leaf")
    return _normalize_plantnet(raw)
