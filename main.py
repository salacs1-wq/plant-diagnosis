# main.py
import os
import uuid
import base64
import mimetypes
from io import BytesIO
from pathlib import Path
from typing import Optional, Literal, Dict, Any, Tuple

import requests
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl

from PIL import Image  # pip install pillow

# ----------------------------
# Config
# ----------------------------
PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_PROJECT = os.getenv("PLANTNET_PROJECT", "all").strip()
PLANTNET_BASE = os.getenv("PLANTNET_BASE", "https://my-api.plantnet.org/v2/identify").rstrip("/")

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/tmp/uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

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
    proto = req.headers.get("x-forwarded-proto", req.url.scheme) or "https"
    host = req.headers.get("x-forwarded-host") or req.headers.get("host")
    if not host:
        fallback = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
        if fallback:
            return fallback
        raise HTTPException(status_code=500, detail="Cannot determine public base URL (missing host headers).")
    return f"{proto}://{host}"


def _decode_base64_image(data: str) -> bytes:
    """
    Accepts either:
    - pure base64
    - data URL: data:image/jpeg;base64,....
    Robust: strips whitespace, accepts urlsafe base64 too.
    """
    if not data:
        raise HTTPException(status_code=400, detail="image_base64 is empty.")

    s = data.strip()
    if s.lower().startswith("data:") and "," in s:
        s = s.split(",", 1)[1]

    # remove whitespace/newlines
    s = "".join(s.split())

    # first try strict
    try:
        return base64.b64decode(s, validate=True)
    except Exception:
        # try urlsafe
        try:
            return base64.urlsafe_b64decode(s + "===")  # padding tolerant
        except Exception:
            # last resort relaxed
            try:
                return base64.b64decode(s)
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid base64 image.")


def _ensure_supported_image(image_bytes: bytes) -> Tuple[bytes, str, str]:
    """
    Validates image bytes with Pillow.
    Converts everything to JPEG (RGB) for PlantNet compatibility.
    Returns: (bytes_out, mime, ext)
    """
    try:
        with Image.open(BytesIO(image_bytes)) as im:
            im.load()
            # Convert to RGB and export as JPEG
            if im.mode in ("RGBA", "P"):
                im = im.convert("RGB")
            elif im.mode != "RGB":
                im = im.convert("RGB")

            out = BytesIO()
            im.save(out, format="JPEG", quality=92, optimize=True)
            return out.getvalue(), "image/jpeg", ".jpg"
    except Exception:
        raise HTTPException(status_code=400, detail="Unsupported file type (cannot decode image).")


def _plantnet_identify_from_bytes(image_bytes: bytes, filename: str, organs: str = "leaf") -> Dict[str, Any]:
    _require_key()

    # Always validate/convert to JPEG before sending upstream
    img_bytes, mime, ext = _ensure_supported_image(image_bytes)

    # Ensure filename extension matches
    safe_name = Path(filename).stem or "image"
    send_filename = f"{safe_name}{ext}"

    url = f"{PLANTNET_BASE}/{PLANTNET_PROJECT}"
    params = {"api-key": PLANTNET_API_KEY}

    # IMPORTANT: include MIME type
    files = {"images": (send_filename, img_bytes, mime)}
    data = {"organs": organs}

    try:
        r = requests.post(url, params=params, files=files, data=data, timeout=60)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"PlantNet upstream error: {e}")

    if r.status_code >= 400:
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


@app.post("/upload")
async def upload_image(request: Request, image: UploadFile = File(...)):
    if not image:
        raise HTTPException(status_code=400, detail="Missing file field: image")

    content = await image.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file.")

    # validate/convert to JPEG
    jpg_bytes, mime, ext = _ensure_supported_image(content)

    name = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_DIR / name
    dest.write_bytes(jpg_bytes)

    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/") or _public_base_url_from_request(request)
    url = f"{base}/files/{name}"
    return {"url": url, "filename": name, "contentType": mime}


@app.post("/upload_b64", response_model=UploadOut)
async def upload_image_b64(request: Request, payload: UploadB64In):
    img_bytes = _decode_base64_image(payload.image_base64)
    jpg_bytes, mime, ext = _ensure_supported_image(img_bytes)

    name = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_DIR / name
    dest.write_bytes(jpg_bytes)

    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/") or _public_base_url_from_request(request)
    url = f"{base}/files/{name}"
    return {"url": url, "filename": name, "contentType": mime}


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

    raw = _plantnet_identify_from_bytes(img_bytes, image.filename or "image.jpg", organs=organs)
    return _normalize_plantnet(raw)


@app.post("/identify_b64")
async def identify_b64(payload: IdentifyB64In):
    img_bytes = _decode_base64_image(payload.image_base64)
    filename = payload.filename or "image.jpg"
    raw = _plantnet_identify_from_bytes(img_bytes, filename, organs=payload.organs or "leaf")
    return _normalize_plantnet(raw)


@app.post("/identify_url")
def identify_by_url(payload: IdentifyByUrlIn):
    _require_key()
    try:
        img = requests.get(str(payload.image_url), timeout=30)
        img.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=400, detail=f"Could not fetch image_url: {e}")

    filename = Path(str(payload.image_url)).name or "image.jpg"
    raw = _plantnet_identify_from_bytes(img.content, filename, organs=payload.organs or "leaf")
    return _normalize_plantnet(raw)
