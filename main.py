import os
import requests
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from starlette.responses import JSONResponse

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_URL = "https://my-api.plantnet.org/v2/identify/all"

app = FastAPI(title="PlantNet Proxy", version="1.0.0")

@app.get("/")
def health():
    return {"status": "ok", "message": "PlantNet proxy fut"}

@app.post("/identify")
async def identify(
    image: UploadFile = File(...),   # a GPT Actions ezt fogja küldeni: image=FILE
    organs: str = Form("leaf"),      # egyszerű: egy darab szöveg
):
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="Missing PLANTNET_API_KEY on server")

    img = await image.read()
    if not img or len(img) < 200:
        raise HTTPException(status_code=400, detail="Empty or too small image")

    # PlantNet felé: csak images + organs (SEMMI MÁS!)
    files = {
        "images": (image.filename or "image.jpg", img, image.content_type or "image/jpeg")
    }
    data = {
        "organs": organs
    }
    params = {"api-key": PLANTNET_API_KEY}

    try:
        r = requests.post(PLANTNET_URL, params=params, data=data, files=files, timeout=60)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"PlantNet network error: {e}")

    # PlantNet hiba esetén adjuk vissza nyersen (így látni fogjuk az okot)
    if r.status_code >= 400:
        try:
            err = r.json()
        except Exception:
            err = {"raw": r.text}
        raise HTTPException(status_code=502, detail={"plantnet_status": r.status_code, "plantnet_error": err})

    out = r.json()

    # Rövidített válasz (top3)
    results = out.get("results") or []
    top = results[:3]
    topMatches = []
    for item in top:
        sp = item.get("species") or {}
        fam = (sp.get("family") or {})
        topMatches.append({
            "score": item.get("score"),
            "scientificName": sp.get("scientificName"),
            "scientificNameWithoutAuthor": sp.get("scientificNameWithoutAuthor"),
            "family": fam.get("scientificNameWithoutAuthor") or fam.get("scientificName"),
            "commonNames": sp.get("commonNames") or [],
        })

    best = out.get("bestMatch") or (topMatches[0]["scientificName"] if topMatches else None)
    return JSONResponse({
        "bestMatch": best,
        "topMatches": topMatches,
        "raw": out
    })
