from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel
import requests
import uuid
import os
import shutil

app = FastAPI()

API_KEY = os.getenv("PLANTNET_API_KEY", "")
PROJECT = "all"


# =========================
# MODELL GPT-hez
# =========================

class DiagnoseRequest(BaseModel):
    openaiFileIdRefs: list | None = None
    mode: str = "weed"
    top_k: int = 5


# =========================
# ROOT
# =========================

@app.get("/")
def root():
    return {"status": "ok", "service": "plant-diagnosis"}


@app.get("/ping")
def ping():
    return {"ping": "pong"}


# =========================
# RÉGI ENDPOINT (Swagger)
# =========================

@app.post("/analyze")
async def analyze(file: UploadFile = File(...), mode: str = "weed"):

    if not API_KEY:
        raise HTTPException(500, "Missing API key")

    temp_name = f"temp_{uuid.uuid4()}.jpg"

    try:

        with open(temp_name, "wb") as f:
            shutil.copyfileobj(file.file, f)

        if mode == "weed":
            url = f"https://my-api.plantnet.org/v2/identify/{PROJECT}?api-key={API_KEY}"
        else:
            url = f"https://my-api.plantnet.org/v2/diseases/identify?api-key={API_KEY}"

        with open(temp_name, "rb") as img:

            files = {
                "images": (temp_name, img, "image/jpeg")
            }

            r = requests.post(url, files=files, timeout=60)

        data = r.json()

        return data

    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)


# =========================
# GPT ENDPOINT
# =========================

@app.post("/diagnoseFiles")
async def diagnose_files(req: DiagnoseRequest):
    mode = (req.mode or "weed").strip().lower()

    if mode not in ["weed", "disease", "pest"]:
        mode = "weed"

    if not req.openaiFileIdRefs:
        return {
            "status": "error",
            "mode": mode,
            "message": "No file received",
            "context_flags": {
                "no_file": True
            }
        }

    return {
        "status": "error",
        "mode": mode,
        "message": "A GPT fájlreferencia megérkezett, de a backend még nem kap valódi képfájlt a PlantNet elemzéshez.",
        "context_flags": {
            "gpt_file_bridge_missing": True
        }
    }
    # ideiglenes: GPT file nem mindig elérhető → demo válasz

    return {
        "status": "ok",
        "mode": mode,
        "species": "Triticum aestivum",
        "label": "Blumeria graminis - Powdery mildew",
        "score": 0.82,
        "raw": {},
        "context_flags": {}
    }
