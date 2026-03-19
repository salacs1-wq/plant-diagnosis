from fastapi import FastAPI
from pydantic import BaseModel
import requests
import os

app = FastAPI()

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY")


# =========================
# REQUEST MODEL
# =========================
class ImageRequest(BaseModel):
    image_url: str


# =========================
# IMAGE DOWNLOAD
# =========================
def download_image(image_url: str):
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    response = requests.get(image_url, headers=headers, timeout=10)
    response.raise_for_status()

    return response.content


# =========================
# PLANTNET CALL
# =========================
def call_plantnet(image_bytes):
    url = f"https://my-api.plantnet.org/v2/identify/all?api-key={PLANTNET_API_KEY}"

    files = {
        "images": ("image.jpg", image_bytes, "image/jpeg")
    }

    data = {
        "organs": "leaf"
    }

    response = requests.post(url, files=files, data=data)
    response.raise_for_status()

    return response.json()


# =========================
# FORMAT TOP5
# =========================
def format_top5(results):
    top5 = []

    for r in results[:5]:
        species = r.get("species", {})
        scientific = species.get("scientificNameWithoutAuthor", "ismeretlen")

        common_names = species.get("commonNames", [])
        hungarian = common_names[0] if common_names else "nincs"

        score = r.get("score", 0)

        top5.append({
            "latin": scientific,
            "hungarian": hungarian,
            "score": round(score, 4)
        })

    return top5


# =========================
# ENDPOINT
# =========================
@app.post("/analyze-weed")
async def analyze_weed(req: ImageRequest):
    try:
        img = download_image(req.image_url)

        plantnet = call_plantnet(img)
        results = plantnet.get("results", [])

        top5 = format_top5(results)

        return {
            "status": "success",
            "mode": "weed",
            "top5": top5
        }

    except Exception as e:
        return {
            "status": "error",
            "mode": "weed",
            "message": str(e)
        }


# =========================
# ROOT
# =========================
@app.get("/")
def root():
    return {"status": "ok", "version": "v2.4-top5"}
