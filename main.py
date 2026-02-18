from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
import base64
import re
import requests

app = FastAPI(title="Plant Diagnosis API")

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY")
PLANTNET_PROJECT = os.getenv("PLANTNET_PROJECT", "weurope")
PLANTNET_URL = f"https://my-api.plantnet.org/v2/identify/{PLANTNET_PROJECT}"


# -----------------------------
# Adatmodell
# -----------------------------
class IdentifyRequest(BaseModel):
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


# -----------------------------
# Health check
# -----------------------------
@app.get("/")
def health():
    return {"status": "ok"}


# -----------------------------
# PlantNet azonosítás
# -----------------------------
@app.post("/identify_b64")
def identify_plant(req: IdentifyRequest):
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="Missing PLANTNET_API_KEY")

    image_bytes = decode_base64_image(req.image_base64)

    files = {
        "images": (req.filename, image_bytes, req.contentType)
    }

    data = {
        "organs": req.organs
    }

    try:
        response = requests.post(
            f"{PLANTNET_URL}?api-key={PLANTNET_API_KEY}",
            files=files,
            data=data,
            timeout=30,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PlantNet connection error: {str(e)}")

    if response.status_code != 200:
        raise HTTPException(
            status_code=500,
            detail=f"PlantNet error: {response.status_code} {response.text}"
        )

    result = response.json()

    if not result.get("results"):
        return {
            "bestMatch": None,
            "confidence": None,
            "topMatches": []
        }

    top = result["results"][0]

    return {
        "bestMatch": top["species"]["scientificNameWithoutAuthor"],
        "confidence": {
            "top1_score": top["score"],
            "level": "species"
        },
        "topMatches": [
            {
                "name": r["species"]["scientificNameWithoutAuthor"],
                "score": r["score"]
            }
            for r in result["results"][:5]
        ]
    }
