from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
import os
import base64
import re
import requests

app = FastAPI(title="Plant Diagnosis API")

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY")
PLANTNET_PROJECT = os.getenv("PLANTNET_PROJECT", "all")  # pl. "weurope" vagy "all"


class IdentifyRequest(BaseModel):
    # FONTOS: a ChatGPT néha ide nem base64-et, hanem fájlutat ad (/mnt/data/xxx.jpg)
    image_base64: str
    filename: str = "image.jpg"
    contentType: str = "image/jpeg"
    organs: str = "leaf"


def _read_if_file_path(s: str) -> bytes | None:
    """Ha s fájlút és létezik, visszaadja a fájl bájtjait, különben None."""
    if not s:
        return None
    s2 = s.strip()
    # tipikus ChatGPT Actions path: /mnt/data/.....
    if (s2.startswith("/") or s2.startswith("mnt/")) and os.path.exists(s2):
        with open(s2, "rb") as f:
            return f.read()
    return None


def decode_base64_image(data: str) -> bytes:
    """
    Elfogad:
    - tiszta base64
    - data URL (data:image/jpeg;base64,...)
    - fájlút (pl. /mnt/data/xxx.jpg)  <-- EZ oldja meg a mostani hibádat
    Javít:
    - whitespace
    - urlsafe base64
    - padding
    """
    if not data or not isinstance(data, str):
        raise HTTPException(status_code=400, detail="image_base64 missing or invalid")

    # 1) ha fájlút, olvassuk be
    file_bytes = _read_if_file_path(data)
    if file_bytes is not None:
        return file_bytes

    s = data.strip()

    # 2) data URL előtag levágása
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


def plantnet_identify(image_bytes: bytes, filename: str, content_type: str, organs: str):
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="Missing PLANTNET_API_KEY")

    url = f"https://my-api.plantnet.org/v2/identify/{PLANTNET_PROJECT}"
    params = {"api-key": PLANTNET_API_KEY}

    files = {"images": (filename, image_bytes, content_type)}
    data = {"organs": organs or "leaf"}

    try:
        r = requests.post(url, params=params, files=files, data=data, timeout=45)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"PlantNet connection error: {str(e)}")

    # PlantNet hibákat adjuk vissza olvashatóan
    if r.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"PlantNet error: {r.status_code} {r.text}"
        )

    result = r.json()

    if not result.get("results"):
        return {"bestMatch": None, "confidence": None, "topMatches": []}

    top = result["results"][0]
    top_matches = []
    for x in result["results"][:5]:
        top_matches.append({
            "name": x["species"]["scientificNameWithoutAuthor"],
            "score": x["score"]
        })

    return {
        "bestMatch": top["species"]["scientificNameWithoutAuthor"],
        "confidence": {"top1_score": top["score"], "level": "species"},
        "topMatches": top_matches
    }


@app.get("/")
def health():
    return {"status": "ok"}


# 1) MULTIPART (ez a legjobb képfeltöltéshez)
@app.post("/identify")
async def identify_plant_multipart(
    image: UploadFile = File(...),
    organs: str = Form("leaf"),
):
    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    content_type = image.content_type or "image/jpeg"
    filename = image.filename or "image.jpg"

    return plantnet_identify(image_bytes, filename, content_type, organs)


# 2) BASE64/JSON (ÉS fájlút kompatibilis)
@app.post("/identify_b64")
def identify_plant_b64(req: IdentifyRequest):
    image_bytes = decode_base64_image(req.image_base64)
    return plantnet_identify(image_bytes, req.filename, req.contentType, req.organs)
