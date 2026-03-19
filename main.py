from fastapi import FastAPI, UploadFile, File
from pydantic import BaseModel
import requests
import os
import uuid

app = FastAPI()

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY")

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


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
    top5 = []

    for r in results[:5]:
        species = r.get("species", {})
        top5.append({
            "latin": species.get("scientificNameWithoutAuthor", "ismeretlen"),
            "score": round(r.get("score", 0), 4)
        })

    return top5


# =========================
# CORE LOGIC (EZ A LÉNYEG)
# =========================
def build_response(mode, top5):
    return {
        "status": "success",
        "mode": mode,
        "top1": top5[0] if top5 else None,
        "top5": top5
    }


# =========================
# ENDPOINTS
# =========================

@app.post("/analyze-weed")
async def analyze_weed(req: ImageRequest):
    try:
        if not req.image_url.startswith("http"):
            return {"status": "error", "message": "Csak publikus URL használható"}

        img = download_image(req.image_url)
        plant = call_plantnet(img)

        top5 = format_top5(plant.get("results", []))

        return build_response("weed", top5)

    except Exception as e:
        return {"status": "error", "mode": "weed", "message": str(e)}


@app.post("/analyze-disease")
async def analyze_disease(req: ImageRequest):
    try:
        if not req.image_url.startswith("http"):
            return {"status": "error", "message": "Csak publikus URL használható"}

        img = download_image(req.image_url)
        plant = call_plantnet(img)

        top5 = format_top5(plant.get("results", []))

        return build_response("disease", top5)

    except Exception as e:
        return {"status": "error", "mode": "disease", "message": str(e)}


@app.post("/analyze-pest")
async def analyze_pest(req: ImageRequest):
    try:
        if not req.image_url.startswith("http"):
            return {"status": "error", "message": "Csak publikus URL használható"}

        img = download_image(req.image_url)
        plant = call_plantnet(img)

        top5 = format_top5(plant.get("results", []))

        return build_response("pest", top5)

    except Exception as e:
        return {"status": "error", "mode": "pest", "message": str(e)}


@app.get("/")
def root():
    return {"status": "ok", "version": "v2.3-stable"}
