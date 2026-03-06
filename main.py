import os
import requests
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware

APP_VERSION = "stable-1.0"

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY")
PLANTNET_PROJECT = os.getenv("PLANTNET_PROJECT", "k-middle-europe")

app = FastAPI(title="Plant Diagnosis API", version=APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {
        "status": "ok",
        "version": APP_VERSION,
        "endpoint": "/v1/diagnose"
    }

@app.get("/health")
def health():
    return {
        "status": "ok",
        "plantnet_project": PLANTNET_PROJECT
    }

@app.post("/v1/diagnose")
async def diagnose(
    image: UploadFile = File(...),
    project: str = Form(PLANTNET_PROJECT),
    organs: str = Form("leaf"),
    top_k: int = Form(5)
):

    image_bytes = await image.read()

    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty image")

    url = f"https://my-api.plantnet.org/v2/identify/{project}"
    params = {"api-key": PLANTNET_API_KEY}

    files = {
        "images": ("image.jpg", image_bytes, "image/jpeg")
    }

    data = {
        "organs": organs
    }

    resp = requests.post(
        url,
        params=params,
        files=files,
        data=data,
        timeout=60
    )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"PlantNet error {resp.status_code}: {resp.text}"
        )

    raw = resp.json()

    results = raw.get("results", [])[:top_k]

    candidates = []

    for r in results:
        species = r.get("species", {})
        candidates.append({
            "scientific_name": species.get("scientificNameWithoutAuthor"),
            "score": r.get("score"),
            "common_names": species.get("commonNames", [])
        })

    return {
        "ok": True,
        "project": project,
        "candidates": candidates
    }
