from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
import os
import base64
import re
import requests

app = FastAPI(title="Plant Diagnosis API")

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY")
PLANTNET_PROJECT = os.getenv("PLANTNET_PROJECT", "all")  # pl. weurope vagy all
PLANTNET_URL_BASE = "https://my-api.plantnet.org/v2/identify"


# -----------------------------
# Adatmodell a base64 endpointhez
# -----------------------------
class IdentifyB64Request(BaseModel):
    image_base64: str
    filename: str = "image.jpg"
    contentType: str = "image/jpeg"
    organs: str = "leaf"


# -----------------------------
# Base64 dekódoló (erősített)
# -----------------------------
def decode_base64_image(data: str) -> bytes:
    """
    Elfogad:
    - tiszta base64-et
    - data:image/jpeg;base64,... formátumot
    Javít:
    - whitespace
    - urlsafe base64
    - padding
    """
    if not data or not isinstance(data, str):
        raise HTTPException(status_code=400, detail="image_base64 missing or invalid")

    s = data.strip()

    # data URL előtag levágása
    if s.lower().startswith("data:"):
        if "," not in s:
            raise HTTPException(status_code=400, detail="Invalid data URL")
        s = s.split(",", 1)[1]

    # whitespace törlés
    s = re.sub(r"\s+", "", s)

    # urlsafe javítás
    s = s.replace("-", "+").replace("_", "/")

    # padding
    pad = (-len(s)) % 4
    if pad:
        s += "=" * pad

    try:
        return base64.b64decode(s, validate=False)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 image")


def call_plantnet(image_bytes: bytes, filename: str, content_type: str, organs: str):
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="Missing PLANTNET_API_KEY")

    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty image")

    url = f"{PLANTNET_URL_BASE}/{PLANTNET_PROJECT}?api-key={PLANTNET_API_KEY}"

    files = {"images": (filename or "image.jpg", image_bytes, content_type or "image/jpeg")}
    data = {"organs": organs or "leaf"}

    try:
        r = requests.post(url, files=files, data=data, timeout=60)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"PlantNet connection error: {str(e)}")

    # PlantNet hibát NE 500-zal temessük el: adjuk vissza értelmezhetően
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"PlantNet error: {r.status_code} {r.text}")

    result = r.json()

    if not result.get("results"):
        return {"bestMatch": None, "confidence": None, "topMatches": []}

    top = result["results"][0]
    return {
        "bestMatch": top["species"]["scientificNameWithoutAuthor"],
        "confidence": {"top1_score": top["score"], "level": "species"},
        "topMatches": [
            {"name": x["species"]["scientificNameWithoutAuthor"], "score": x["score"]}
            for x in result["results"][:5]
        ],
    }


# -----------------------------
# Health check
# -----------------------------
@app.get("/")
def health():
    return {"status": "ok"}


# -----------------------------
# ✅ EZ KELL Actions-nek: fájlfeltöltés (multipart)
# -----------------------------
@app.post("/identify")
async def identify(
    image: UploadFile = File(...),
    organs: str = Form("leaf"),
):
    try:
        image_bytes = await image.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cannot read uploaded file: {str(e)}")

    return call_plantnet(
        image_bytes=image_bytes,
        filename=image.filename or "image.jpg",
        content_type=image.content_type or "image/jpeg",
        organs=organs,
    )


# -----------------------------
# (opcionális) base64 endpoint: ha egyszer tényleg base64-et küldesz
# -----------------------------
@app.post("/identify_b64")
def identify_b64(req: IdentifyB64Request):
    image_bytes = decode_base64_image(req.image_base64)
    return call_plantnet(
        image_bytes=image_bytes,
        filename=req.filename,
        content_type=req.contentType,
        organs=req.organs,
    )
