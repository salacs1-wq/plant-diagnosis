from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
import base64
import re
import requests

app = FastAPI(title="Plant Diagnosis API")

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_PROJECT = os.getenv("PLANTNET_PROJECT", "all").strip()
PLANTNET_BASE = "https://my-api.plantnet.org/v2/identify"


class IdentifyRequest(BaseModel):
    image_base64: str
    filename: str = "image.jpg"
    contentType: str = "image/jpeg"
    organs: str = "leaf"


def _sniff_mime(img: bytes) -> str:
    # JPEG
    if img[:2] == b"\xff\xd8":
        return "image/jpeg"
    # PNG
    if img[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    # WEBP: RIFF....WEBP
    if img[:4] == b"RIFF" and img[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def decode_base64_image(data: str) -> bytes:
    if not data or not isinstance(data, str):
        raise HTTPException(status_code=400, detail="image_base64 missing or invalid")

    s = data.strip()

    # data URL levágása
    if s.lower().startswith("data:"):
        if "," not in s:
            raise HTTPException(status_code=400, detail="Invalid data URL (missing comma)")
        s = s.split(",", 1)[1]

    # whitespace törlés
    s = re.sub(r"\s+", "", s)

    # urlsafe -> standard
    s = s.replace("-", "+").replace("_", "/")

    # padding javítás
    pad = (-len(s)) % 4
    if pad:
        s += "=" * pad

    try:
        return base64.b64decode(s, validate=False)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 image")


@app.get("/")
def health():
    return {"status": "ok"}


@app.post("/identify_b64")
def identify_plant(req: IdentifyRequest):
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="Missing PLANTNET_API_KEY")

    img_bytes = decode_base64_image(req.image_base64)

    mime = _sniff_mime(img_bytes)
    if mime not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(status_code=400, detail=f"Unsupported decoded image type: {mime}")

    # filename kiterjesztés igazítása
    if mime == "image/jpeg":
        filename = "image.jpg"
    elif mime == "image/png":
        filename = "image.png"
    else:
        filename = "image.webp"

    url = f"{PLANTNET_BASE}/{PLANTNET_PROJECT}"
    params = {"api-key": PLANTNET_API_KEY}

    files = {"images": (filename, img_bytes, mime)}
    data = {"organs": req.organs or "leaf"}

    try:
        resp = requests.post(url, params=params, files=files, data=data, timeout=60)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"PlantNet connection error: {str(e)}")

    if resp.status_code >= 400:
        # ezt nagyon fontos visszaadni, mert ebből látjuk mi a PlantNet baja
        raise HTTPException(status_code=502, detail=f"PlantNet error: {resp.status_code} {resp.text}")

    return resp.json()
