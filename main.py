from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
import base64
import re
import requests

app = FastAPI(title="Plant Diagnosis API")

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY")
PLANTNET_PROJECT = os.getenv("PLANTNET_PROJECT", "all")  # pl. "weurope" vagy "all"

# Ha régiót akarsz: pl. https://my-api.plantnet.org/v2/identify/weurope
PLANTNET_URL_BASE = "https://my-api.plantnet.org/v2/identify"


class IdentifyRequest(BaseModel):
    image_base64: str
    filename: str = "image.jpg"
    contentType: str = "image/jpeg"
    organs: str = "leaf"


def load_image_bytes(image_field: str) -> bytes:
    """
    Elfogad:
    - /mnt/data/... fájlútvonalat (ChatGPT file upload tipikus)
    - tiszta base64-et
    - data:image/jpeg;base64,... formátumot
    """
    if not image_field or not isinstance(image_field, str):
        raise HTTPException(status_code=400, detail="image_base64 missing or invalid")

    s = image_field.strip()

    # 1) Ha fájlútvonal érkezik
    if s.startswith("/mnt/") or s.startswith("./") or s.startswith("/tmp/"):
        try:
            with open(s, "rb") as f:
                return f.read()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Cannot read image file path: {str(e)}")

    # 2) Különben base64-nek tekintjük
    if s.lower().startswith("data:"):
        if "," not in s:
            raise HTTPException(status_code=400, detail="Invalid data URL")
        s = s.split(",", 1)[1]

    s = re.sub(r"\s+", "", s)
    s = s.replace("-", "+").replace("_", "/")
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

    image_bytes = load_image_bytes(req.image_base64)

    files = {"images": (req.filename, image_bytes, req.contentType)}
    data = {"organs": req.organs}

    # Projekt: "weurope" vagy "all"
    url = f"{PLANTNET_URL_BASE}/{PLANTNET_PROJECT}?api-key={PLANTNET_API_KEY}"

    try:
        response = requests.post(url, files=files, data=data, timeout=30)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PlantNet connection error: {str(e)}")

    if response.status_code != 200:
        raise HTTPException(status_code=500, detail=f"PlantNet error: {response.status_code} {response.text}")

    result = response.json()
    if not result.get("results"):
        return {"bestMatch": None, "confidence": None, "topMatches": []}

    top = result["results"][0]
    return {
        "bestMatch": top["species"]["scientificNameWithoutAuthor"],
        "confidence": {"top1_score": top["score"], "level": "species"},
        "topMatches": [
            {"name": r["species"]["scientificNameWithoutAuthor"], "score": r["score"]}
            for r in result["results"][:5]
        ],
    }
