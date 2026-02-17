# main.py
import os
import re
import uuid
import base64
import binascii
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
PLANTNET_PROJECT = os.getenv("PLANTNET_PROJECT", "all").strip()
PLANTNET_BASE = os.getenv("PLANTNET_BASE", "https://my-api.plantnet.org/v2/identify").rstrip("/")

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/tmp/uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}


# ----------------------------
# App
# ----------------------------
app = FastAPI(title="Plant Diagnosis API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/files", StaticFiles(directory=str(UPLOAD_DIR), html=False), name="files")


# ----------------------------
# Helpers
# ----------------------------
def _require_key():
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="Missing PLANTNET_API_KEY env var on the server.")


def _public_base_url_from_request(req: Request) -> str:
    # Render behind proxy
    proto = req.headers.get("x-forwarded-proto", req.url.scheme) or "https"
    host = req.headers.get("x-forwarded-host") or req.headers.get("host")
    if not host:
        fallback = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
        if fallback:
            return fallback
        raise HTTPException(status_code=500, detail="Cannot determine public base URL (missing host headers).")
    return f"{proto}://{host}"


def _guess_ext(filename: Optional[str], content_type: Optional[str]) -> str:
    if filename:
        ext = Path(filename).suffix.lower()
        if ext in ALLOWED_EXT:
            return ext

    if content_type:
        ct = content_type.lower().strip()
        if ct == "image/jpg":
            ct = "image/jpeg"
        guess = mimetypes.guess_extension(ct)
        if guess and guess.lower() in ALLOWED_EXT:
            return guess.lower()

    return ".jpg"


def _sniff_image_type(img_bytes: bytes) -> str:
    """
    Returns: 'jpeg', 'png', 'webp' or raises 400 if unknown.
    """
    kind = imghdr.what(None, h=img_bytes)
    if kind == "jpeg":
        return "jpeg"
    if kind == "png":
        return "png"
    if kind == "webp":
        return "webp"
    raise HTTPException(status_code=400, detail="Unsupported image bytes (not jpeg/png/webp).")


def _content_type_from_kind(kind: str) -> str:
    if kind == "jpeg":
        return "image/jpeg"
    if kind == "png":
        return "image/png"
    if kind == "webp":
        return "image/webp"
    return "application/octet-stream"


def _decode_base64_image(data: str) -> bytes:
    """
    Accepts either:
    - pure base64
    - data URL: data:image/jpeg;base64,....
    Tolerates whitespace/newlines and missing padding.
    """
    if not data or not isinstance(data, str):
        raise HTTPException(status_code=400, detail="image_base64 is empty.")

    s = data.strip()

    # data URL prefix
    if s.lower().startswith("data:"):
        if "," not in s:
            raise HTTPException(status_code=400, detail="Invalid data URL (missing comma).")
        s = s.split(",", 1)[1]

    # remove whitespace/newlines
    s = re.sub(r"\s+", "", s)

    # add missing padding if needed
    missing = (-len(s)) % 4
    if missing:
        s += "=" * missing

    try:
        img_bytes = base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="Invalid base64 image.")

    # validate that bytes are a supported image
    _sniff_image_type(img_bytes)
    return img_bytes


def _plantnet_identify_from_bytes(
    image_bytes: bytes,
    filename: str,
    organs: str = "leaf",
    content_type: str = "image/jpeg",
) -> Dict[str, Any]:
    _require_key()

    url = f"{PLANTNET_BASE}/{PLANTNET_PROJECT}"
    params = {"api-key": PLANTNET_API_KEY}

    # PlantNet expects "images" as the multipart key
    files = {"images": (filename or "image.jpg", image_bytes, content_type)}
    data = {"organs": organs}

    try:
        r = requests.post(url, params=params, files=files, data=data, timeout=60)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"PlantNet upstream error: {e}")

    if r.status_code >= 400:
        # return PlantNet message so you see the real reason
        raise HTTPException(status_code=502, detail=f"PlantNet error: {r.status_code} {r.text}")

    return r.json()


def _normalize_plantnet(raw: Dict[str, Any]) -> Dict[str, Any]:
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

    level = "alacsony"
    if isinstance(best_score, (int, float)):
        if best_score >= 0.7:
            level = "magas"
        elif best_score >= 0.45:
            level = "kozepes"

    return {
        "bestMatch": best_match,
        "confidence": {"top1_score": best_score, "level": level},
        "topMatches": top_matches,
        "raw": raw,
    }


# ----------------------------
# Schemas
# ----------------------------
class UploadB64In(BaseModel):
    image_base64: str
    filename: Optional[str] = None
    contentType: Optional[str] = None


class UploadOut(BaseModel):
    url: str
    filename: str
    contentType: Optional[str] = None


class IdentifyByUrlIn(BaseModel):
    image_url: HttpUrl
    organs: Optional[Literal["leaf", "flower", "fruit", "bark"]] = "leaf"


class IdentifyB64In(BaseModel):
    image_base64: str
    filename: Optional[str] = None
    contentType: Optional[str] = None
    organs: Optional[Literal["leaf", "flower", "fruit", "bark"]] = "leaf"


# ----------------------------
# Routes
# ----------------------------
@app.get("/")
def health():
    return {"status": "ok", "message": "Plant Diagnosis API running"}


# Kept for Postman/Hoppscotch/manual tests (multipart) – GPT Actions UI sometimes doesn't render a file picker
@app.post("/upload")
async def upload_image(request: Request, image: UploadFile = File(...)):
    if not image:
        raise HTTPException(status_code=400, detail="Missing file field: image")

    content = await image.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file.")

    # Validate bytes
    kind = _sniff_image_type(content)
    ct = image.content_type or _content_type_from_kind(kind)

    ext = _guess_ext(image.filename, ct)
    name = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_DIR / name
    dest.write_bytes(content)

    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/") or _public_base_url_from_request(request)
    url = f"{base}/files/{name}"
    return {"url": url, "filename": name, "contentType": ct}


# Reliable upload for GPT Actions: JSON base64
@app.post("/upload_b64", response_model=UploadOut)
async def upload_image_b64(request: Request, payload: UploadB64In):
    img_bytes = _decode_base64_image(payload.image_base64)

    # detect true kind from bytes
    kind = _sniff_image_type(img_bytes)
    detected_ct = _content_type_from_kind(kind)

    # prefer provided contentType only if it looks valid; otherwise use detected
    ct = (payload.contentType or "").strip().lower()
    if ct == "image/jpg":
        ct = "image/jpeg"
    if ct not in {"image/jpeg", "image/png", "image/webp"}:
        ct = detected_ct

    ext = _guess_ext(payload.filename, ct)
    name = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_DIR / name
    dest.write_bytes(img_bytes)

    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/") or _public_base_url_from_request(request)
    url = f"{base}/files/{name}"
    return {"url": url, "filename": name, "contentType": ct}


@app.post("/identify")
async def identify_from_upload(
    image: UploadFile = File(...),
    organs: str = Form("leaf"),
):
    if not image:
        raise HTTPException(status_code=400, detail="Missing file field: image")

    img_bytes = await image.read()
    if not img_bytes:
        raise HTTPException(status_code=400, detail="Empty file.")

    kind = _sniff_image_type(img_bytes)
    ct = image.content_type or _content_type_from_kind(kind)

    filename = image.filename or ("image.jpg" if kind == "jpeg" else f"image.{kind}")
    raw = _plantnet_identify_from_bytes(img_bytes, filename, organs=organs, content_type=ct)
    return _normalize_plantnet(raw)


# Reliable identify directly from base64 JSON (no upload step needed)
@app.post("/identify_b64")
async def identify_b64(payload: IdentifyB64In):
    img_bytes = _decode_base64_image(payload.image_base64)

    # detect real image type
    kind = _sniff_image_type(img_bytes)
    detected_ct = _content_type_from_kind(kind)

    # contentType from GPT may be wrong/empty -> fallback to detected
    ct = (payload.contentType or "").strip().lower()
    if ct == "image/jpg":
        ct = "image/jpeg"
    if ct not in {"image/jpeg", "image/png", "image/webp"}:
        ct = detected_ct

    # choose filename / extension consistent with actual bytes
    filename = payload.filename
    if not filename:
        filename = "image.jpg" if kind == "jpeg" else f"image.{kind}"

    raw = _plantnet_identify_from_bytes(
        img_bytes,
        filename,
        organs=payload.organs or "leaf",
        content_type=ct,
    )
    return _normalize_plantnet(raw)


@app.post("/identify_url")
def identify_by_url(payload: IdentifyByUrlIn):
    _require_key()
    try:
        img = requests.get(str(payload.image_url), timeout=30)
        img.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=400, detail=f"Could not fetch image_url: {e}")

    # validate bytes + infer type
    kind = _sniff_image_type(img.content)
    ct = _content_type_from_kind(kind)

    filename = Path(str(payload.image_url)).name
    if not filename:
        filename = "image.jpg" if kind == "jpeg" else f"image.{kind}"

    raw = _plantnet_identify_from_bytes(img.content, filename, organs=payload.organs or "leaf", content_type=ct)
    return _normalize_plantnet(raw)
