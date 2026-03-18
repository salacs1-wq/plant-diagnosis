from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests

app = FastAPI()


# =========================
# REQUEST MODEL
# =========================
class ImageRequest(BaseModel):
    image_url: str


# =========================
# IMAGE DOWNLOAD (STABIL)
# =========================
def download_image(image_url: str):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0"
        }

        response = requests.get(image_url, headers=headers, timeout=10)
        response.raise_for_status()

        print("IMAGE SIZE:", len(response.content))

        return response.content

    except Exception as e:
        raise Exception(f"Image download error: {str(e)}")


# =========================
# ENDPOINTS
# =========================

@app.post("/analyze-weed")
async def analyze_weed(req: ImageRequest):
    try:
        img = download_image(req.image_url)

        return {
            "status": "success",
            "mode": "weed",
            "image_size": len(img)
        }

    except Exception as e:
        return {
            "status": "error",
            "mode": "weed",
            "message": str(e)
        }


@app.post("/analyze-disease")
async def analyze_disease(req: ImageRequest):
    try:
        img = download_image(req.image_url)

        return {
            "status": "success",
            "mode": "disease",
            "image_size": len(img)
        }

    except Exception as e:
        return {
            "status": "error",
            "mode": "disease",
            "message": str(e)
        }


@app.post("/analyze-pest")
async def analyze_pest(req: ImageRequest):
    try:
        img = download_image(req.image_url)

        return {
            "status": "success",
            "mode": "pest",
            "image_size": len(img)
        }

    except Exception as e:
        return {
            "status": "error",
            "mode": "pest",
            "message": str(e)
        }


# =========================
# HEALTH CHECK
# =========================
@app.get("/")
def root():
    return {
        "status": "ok",
        "version": "v2.3-stable"
    }
