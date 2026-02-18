from fastapi import FastAPI, HTTPException, UploadFile, File, Form
import os
import requests

app = FastAPI(title="Plant Diagnosis API")

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY")
PLANTNET_PROJECT = os.getenv("PLANTNET_PROJECT", "all")  # pl. weurope vagy all
PLANTNET_URL_BASE = "https://my-api.plantnet.org/v2/identify"


@app.get("/")
def health():
    return {"status": "ok"}


@app.post("/identify")
async def identify(
    image: UploadFile = File(...),
    organs: str = Form("leaf"),
):
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="Missing PLANTNET_API_KEY")

    try:
        image_bytes = await image.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cannot read uploaded file: {str(e)}")

    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty image")

    files = {
        "images": (image.filename or "image.jpg", image_bytes, image.content_type or "image/jpeg")
    }
    data = {"organs": organs}

    url = f"{PLANTNET_URL_BASE}/{PLANTNET_PROJECT}?api-key={PLANTNET_API_KEY}"

    try:
        r = requests.post(url, files=files, data=data, timeout=60)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PlantNet connection error: {str(e)}")

    if r.status_code != 200:
        raise HTTPException(status_code=500, detail=f"PlantNet error: {r.status_code} {r.text}")

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
