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
# SZÁNTÓFÖLDI GYOM SZŰRÉS
# =========================
FIELD_WEEDS = [
    "Poa", "Lolium", "Avena", "Setaria",
    "Echinochloa", "Digitaria", "Bromus",
    "Cirsium", "Chenopodium", "Amaranthus",
    "Capsella", "Stellaria"
]

def filter_field_weeds(top5):
    filtered = []

    for item in top5:
        latin = item["latin"]

        if any(genus in latin for genus in FIELD_WEEDS):
            filtered.append(item)

    return filtered


# =========================
# ENDPOINTS
# =========================

@app.post("/analyze-weed")
async def analyze_weed(req: ImageRequest):
    try:
        # 🔥 URL VALIDÁCIÓ (GPT fix)
        if not req.image_url.startswith("http"):
            return {
                "status": "error",
                "mode": "weed",
                "message": "Érvénytelen kép URL. Csak publikus (http/https) link használható."
            }

        img = download_image(req.image_url)

        plantnet = call_plantnet(img)
        results = plantnet.get("results", [])

        top5 = format_top5(results)
        filtered = filter_field_weeds(top5)

        if not filtered:
            return {
                "status": "warning",
                "mode": "weed",
                "message": "Nem szántóföldi növény",
                "top5": top5
            }

        return {
            "status": "success",
            "mode": "weed",
            "top5": top5,
            "field_candidates": filtered
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
        if not req.image_url.startswith("http"):
            return {
                "status": "error",
                "mode": "disease",
                "message": "Érvénytelen kép URL."
            }

        return {
            "status": "success",
            "mode": "disease",
            "message": "Betegség mód még fejlesztés alatt"
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
        if not req.image_url.startswith("http"):
            return {
                "status": "error",
                "mode": "pest",
                "message": "Érvénytelen kép URL."
            }

        return {
            "status": "success",
            "mode": "pest",
            "message": "Kártevő mód még fejlesztés alatt"
        }

    except Exception as e:
        return {
            "status": "error",
            "mode": "pest",
            "message": str(e)
        }


# =========================
# ROOT
# =========================
@app.get("/")
def root():
    return {
        "status": "ok",
        "version": "v2.5-final"
    }
