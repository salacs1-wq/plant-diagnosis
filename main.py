import os
import base64
import mimetypes
from typing import Optional, Literal, Dict, Any

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ----------------------------
# Config
# ----------------------------
PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_PROJECT = os.getenv("PLANTNET_PROJECT", "all").strip()
PLANTNET_BASE = os.getenv("PLANTNET_BASE", "https://my-api.plantnet.org/v2/identify").rstrip("/")

ALLOWED_CT = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}

# ----------------------------
# App
# ----------------------------
app = FastAPI(title="Plant Diagnosis API", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------
# Helpers
# ----------------------------
def _require_key():
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="Missing PLANTNET_API_KEY env var on the server.")

def _safe_guess_filename(filename: Optional[str], content_type: Optional[str]) -> str:
    # Prefer filename extension if allowed, otherwise guess from content-type, else default jpg
    ext = ""
    if filename:
        ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXT:
        if content_type:
            guessed = mimetypes.guess_extension(content_type)
            if guessed and guessed.lower() in ALLOWED_EXT:
                ext = guessed.lower()
        if ext not in ALLOWED_EXT:
            ext = ".jpg"
    return f"image{ext}"

def _decode_base64_image(data: str) -> bytes:
    """
    Accepts:
    - pure base64
    - data URLs
    - GPT shortened base64 blobs
    """
    if not data:
        raise HTTPException(status_code=400, detail="image_base64 is empty.")

    # data URL kezelés
    if "," in data and data.strip().lower().startswith("data:"):
        data = data.split(",", 1)[1]

    # whitespace törlés
    data = data.strip().replace("\n", "").replace("\r", "")

    try:
        return base64.b64decode(data + "===")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 image.")

    """
    Accepts:
    - pure base64
    - data URL: data:image/jpeg;base64,....
    Also tolerates missing padding and whitespace/newlines.
    """
    if not data or not data.strip():
        raise HTTPException(status_code=400, detail="image_base64 is empty.")

    s = data.strip()

    # If data URL, strip prefix
    if s.lower().startswith("data:") and "," in s:
        s = s.split(",", 1)[1].strip()

    # Remove whitespace/newlines
    s = "".join(s.split())

    # Fix missing padding
    pad = (-len(s)) % 4
    if pad:
        s += "=" * pad

    # First try strict
    try:
        return base64.b64decode(s, validate=True)
    except Exception:
        # Fallback relaxed
        try:
            return base64.b64decode(s)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid base64 image.")

def _plantnet_identify_from_bytes(image_bytes: bytes, filename: str, organs: str) -> Dict[str, Any]:
    _require_key()

    # PlantNet endpoint: /v2/identify/{project}?api-key=...
    url = f"{PLANTNET_BASE}/{PLANTNET_PROJECT}"
    params = {"api-key": PLANTNET_API_KEY}

    # PlantNet expects 'images' and 'organs'
    files = {"images": (filename, image_bytes)}
    data = {"organs": organs}

    try:
        r = requests.post(url, params=params, files=files, data=data, timeout=60)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"PlantNet upstream error: {e}")

    if r.status_code >= 400:
        # This is IMPORTANT for debugging: return PlantNet body
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
class IdentifyB64In(BaseModel):
    image_base64: str
    filename: Optional[str] = None
    contentType: Optional[str] = None
    organs: Optional[Literal["leaf", "flower", "fruit", "bark"]] = "leaf"

class IdentifyOut(BaseModel):
    bestMatch: Optional[str] = None
    confidence: Dict[str, Any]
    topMatches: list
    raw: Dict[str, Any]

# ----------------------------
# Routes
# ----------------------------
@app.get("/")
def health():
    return {"status": "ok", "message": "Plant Diagnosis API running"}

@app.post("/identify_b64", response_model=IdentifyOut)
def identify_b64(payload: IdentifyB64In):
    img_bytes = _decode_base64_image(payload.image_base64)

    # contentType check (optional, but helps catch garbage)
    if payload.contentType and payload.contentType.lower() not in ALLOWED_CT:
        # DON'T hard-fail here: phones sometimes lie; only warn by continuing
        pass

    filename = _safe_guess_filename(payload.filename, payload.contentType)
    organs = payload.organs or "leaf"

    raw = _plantnet_identify_from_bytes(img_bytes, filename, organs=organs)
    return _normalize_plantnet(raw)
