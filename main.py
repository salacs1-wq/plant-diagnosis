from fastapi import FastAPI, HTTPException, UploadFile, File, Form
import os
import requests

app = FastAPI(title="Plant Diagnosis API", version="1.0.0")

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY")

# PlantNet project: pl. "weurope" vagy "all"
PLANTNET_PROJECT = (os.getenv("PLANTNET_PROJECT") or "all").strip()

PLANTNET_URL_BASE = "https://my-api.plantnet.org/v2/identify"


@app.get("/")
def health():
    return {"status": "ok"}


@app.post("/identify")
async def identify(
    image: UploadFile = File(..., description="Plant photo (jpg/png)"),
    organs: str = Form("leaf"),
):
    """
    Multipart/form-data endpoint:
    - image: file
    - organs: string (default: leaf)
    """
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="Missing PLANTNET_API_KEY")

    # Olvasás
    try:
        image_bytes = await image.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cannot read uploaded file: {str(e)}")

    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty image")

    # Minimális content-type védelem (nem kötelező, de segít)
    content_type = (image.content_type or "").lower()
    if content_type and not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail=f"Invalid contentType: {image.content_type}")

    filename = image.filename or "image.jpg"
    mime = image.content_type or "image/jpeg"

    # PlantNet request (multipart)
    files = {
        "images": (filename, image_bytes, mime)
    }
    data = {"organs": organs}

    # URL: /v2/identify/{project}?api-key=...
    url = f"{PLANTNET_URL_BASE}/{PLANTNET_PROJECT}?api-key={PLANTNET_API_KEY}"

    try:
        r = requests.post(url, files=files, data=data, timeout=60)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"PlantNet connection error: {str(e)}")

    # PlantNet hibát NE 500-zuk “össze”, adjuk vissza rendesen
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text)

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
