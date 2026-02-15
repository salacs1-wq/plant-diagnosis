# main.py
import os
import json
from typing import List, Optional

import requests
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()

# My Pl@ntNet API (species identification)
PLANTNET_IDENTIFY_URL = "https://my-api.plantnet.org/v2/identify/all"

app = FastAPI(
    title="Plant Diagnosis API (PlantNet proxy)",
    version="1.0.0",
    description="Pl@ntNet (My API) alapú növényazonosítás kép-feltöltéssel (proxy).",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def health_get():
    return {"status": "ok", "message": "Növénydiagnosztikai API fut"}


# Render néha HEAD / kérést küld — legyen rá válasz (különben 405-öt látsz a logban)
@app.head("/")
def health_head():
    return JSONResponse({"status": "ok"})


@app.post("/identify")
async def identify(
    # FONTOS: a bemeneti mező neve "image" (ezt küldi a GPT Actions / Hoppscotch is)
    image: UploadFile = File(..., description="A feltöltött kép (JPG/PNG)."),
    # Egyszerűen így: Hoppscotch-ban organs=leaf
    organs: Optional[str] = Form(default="leaf", description="Pl.: leaf, flower, fruit, bark"),
):
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="Hiányzik a PLANTNET_API_KEY környezeti változó a Renderen.")

    try:
        img_bytes = await image.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Nem tudtam beolvasni a képfájlt: {e}")

    if not img_bytes or len(img_bytes) < 200:
        raise HTTPException(status_code=400, detail="Üres vagy túl kicsi képfájl érkezett.")

    # PlantNet felé: csak organs + images (SEMMI extra mező!)
    data = {"organs": [organs]}  # listát vár
    files = {
        "images": (image.filename or "image.jpg", img_bytes, image.content_type or "image/jpeg")
    }
    params = {"api-key": PLANTNET_API_KEY}

    try:
        r = requests.post(
            PLANTNET_IDENTIFY_URL,
            params=params,
            data=data,
            files=files,
            timeout=60,
        )
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"PlantNet hálózati hiba: {e}")

    # PlantNet hiba
    if r.status_code >= 400:
        try:
            err_json = r.json()
        except Exception:
            err_json = {"raw": r.text}
        raise HTTPException(
            status_code=502,
            detail={"plantnet_status": r.status_code, "plantnet_error": err_json},
        )

    try:
        out = r.json()
    except Exception:
        raise HTTPException(status_code=502, detail="PlantNet nem JSON választ adott (váratlan).")

    results = out.get("results") or []
    top = results[:3]

    simplified_top = []
    for item in top:
        sp = item.get("species") or {}
        fam = sp.get("family") or {}
        simplified_top.append(
            {
                "score": item.get("score"),
                "scientificName": sp.get("scientificName"),
                "scientificNameWithoutAuthor": sp.get("scientificNameWithoutAuthor"),
                "family": fam.get("scientificNameWithoutAuthor") or fam.get("scientificName"),
                "commonNames": sp.get("commonNames") or [],
            }
        )

    best_match = out.get("bestMatch")
    if not best_match and simplified_top:
        best_match = simplified_top[0].get("scientificName")

    top1 = simplified_top[0]["score"] if len(simplified_top) > 0 else None
    top2 = simplified_top[1]["score"] if len(simplified_top) > 1 else None
    gap = (top1 - top2) if (top1 is not None and top2 is not None) else None

    level = "alacsony"
    if top1 is not None:
        if top1 >= 0.7:
            level = "magas"
        elif top1 >= 0.4:
            level = "közepes"

    return JSONResponse(
        {
            "bestMatch": best_match,
            "confidence": {"top1_score": top1, "level": level, "top1_top2_gap": gap},
            "topMatches": simplified_top,
            "meta": {"organs": organs},
            "raw": out,
        }
    )
