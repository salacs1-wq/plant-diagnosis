from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
from PIL import Image
from io import BytesIO

app = FastAPI()

# =========================
# REQUEST MODEL
# =========================
class ImageRequest(BaseModel):
    image_url: str


# =========================
# IMAGE DOWNLOAD
# =========================
def download_image(image_url: str):
    try:
        response = requests.get(image_url, timeout=10)
        response.raise_for_status()
        return Image.open(BytesIO(response.content))
    except Exception as e:
        raise Exception(f"Image download error: {str(e)}")


# =========================
# MOCK ANALYSIS (IDE JÖN A VALÓDI LOGIKA)
# =========================
def analyze_image_stub(mode: str):
    return {
        "status": "success",
        "mode": mode,
        "result": {
            "top1": "minta találat",
            "confidence": 0.87
        }
    }


# =========================
# ENDPOINTS
# =========================
@app.post("/analyze-weed")
async def analyze_weed(req: ImageRequest):
    if not req.image_url:
        raise HTTPException(400, "No image_url provided")

    image = download_image(req.image_url)

    result = analyze_image_stub("weed")
    return result


@app.post("/analyze-disease")
async def analyze_disease(req: ImageRequest):
    if not req.image_url:
        raise HTTPException(400, "No image_url provided")

    image = download_image(req.image_url)

    result = analyze_image_stub("disease")
    return result


@app.post("/analyze-pest")
async def analyze_pest(req: ImageRequest):
    if not req.image_url:
        raise HTTPException(400, "No image_url provided")

    image = download_image(req.image_url)

    result = analyze_image_stub("pest")
    return result


# =========================
# HEALTH CHECK
# =========================
@app.get("/")
def root():
    return {"status": "ok", "version": "v2.3"}
