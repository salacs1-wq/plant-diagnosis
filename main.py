from fastapi import FastAPI
from pydantic import BaseModel
import requests
import os

app = FastAPI()

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY")


# =========================
# MODEL
# =========================
class ImageRequest(BaseModel):
    image_url: str


# =========================
# HELPERS
# =========================
def download_image(image_url: str):
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(image_url, headers=headers)
    r.raise_for_status()
    return r.content


def call_plantnet(image_bytes):
    url = f"https://my-api.plantnet.org/v2/identify/all?api-key={PLANTNET_API_KEY}"

    files = {"images": ("image.jpg", image_bytes, "image/jpeg")}
    data = {"organs": "leaf"}

    r = requests.post(url, files=files, data=data)
    r.raise_for_status()

    return r.json()


def format_top5(results):
    out = []
    for r in results[:5]:
        species = r.get("species", {})
        out.append({
            "latin": species.get("scientificNameWithoutAuthor", "ismeretlen"),
            "score": round(r.get("score", 0), 4)
        })
    return out


# =========================
# 1. GYOM VÉGPONT
# =========================
@app.post("/analyze-weed")
async def analyze_weed(req: ImageRequest):
    try:
        if not req.image_url.startswith("http"):
            return {"status": "error", "message": "Csak publikus URL"}

        img = download_image(req.image_url)
        plant = call_plantnet(img)

        top5 = format_top5(plant.get("results", []))

        return {
            "status": "success",
            "mode": "weed",
            "top5": top5
        }

    except Exception as e:
        return {"status": "error", "mode": "weed", "message": str(e)}


# =========================
# 2. GENERAL (BETEGSÉG + KÁRTEVŐ)
# =========================
@app.post("/analyze-general")
async def analyze_general(req: ImageRequest):
    try:
        if not req.image_url.startswith("http"):
            return {"status": "error", "message": "Csak publikus URL"}

        img = download_image(req.image_url)
        plant = call_plantnet(img)

        top5 = format_top5(plant.get("results", []))

        return {
            "status": "success",
            "mode": "general",
            "raw_results": plant.get("results", []),
            "top5": top5
        }

    except Exception as e:
        return {"status": "error", "mode": "general", "message": str(e)}


@app.get("/")
def root():
    return {"status": "ok", "version": "v2.4-clean"}
