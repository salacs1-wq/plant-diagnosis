import os
from typing import List, Optional
from io import BytesIO

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from pydantic import BaseModel

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_IDENTIFY_URL = "https://my-api.plantnet.org/v2/identify/all"

app = FastAPI(
    title="Plant Diagnosis API (PlantNet proxy)",
    version="2.0.0",
    description="PlantNet alapú növényazonosítás GPT Actions kompatibilis formátumban.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def health():
    return {"status": "ok", "message": "Növénydiagnosztikai API fut"}


# -------- GPT ACTIONS REQUEST MODEL --------

class FileRef(BaseModel):
    download_link: str

class IdentifyRequest(BaseModel):
    openaiFileIdRefs: List[FileRef]
    organs: Optional[List[str]] = ["leaf"]


@app.post("/identify")
def identify(req: IdentifyRequest):
    if not PLANTNET_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Hiányzik a PLANTNET_API_KEY környezeti változó."
        )

    if not req.openaiFileIdRefs:
        raise HTTPException(status_code=400, detail="Nincs feltöltött kép.")

    image_url = req.openaiFileIdRefs[0].download_link

    try:
        img_response = requests.get(image_url, timeout=30)
        img_response.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Kép letöltési hiba: {e}")

    img_bytes = img_response.content

    files = {
        "images": ("image.jpg", img_bytes, "image/jpeg")
    }

    params = {"api-key": PLANTNET_API_KEY}

    data = {
        "organs": req.organs,
        "includeRelatedImages": "false",
        "noReject": "false",
    }

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

    if r.status_code >= 400:
        try:
            err_json = r.json()
        except Exception:
            err_json = {"raw": r.text}
        raise HTTPException(
            status_code=502,
            detail={"plantnet_status": r.status_code, "plantnet_error": err_json},
        )

    out = r.json()
    results = out.get("results", []) or []
    top = results[:3]

    def pick_common_names(item):
        sp = (item.get("species") or {})
        return sp.get("commonNames") or []

    def pick_family(item):
        sp = (item.get("species") or {})
        fam = sp.get("family") or {}
        return fam.get("scientificNameWithoutAuthor") or fam.get("scientificName") or None

    simplified_top = []
    for item in top:
        sp = item.get("species") or {}
        simplified_top.append(
            {
                "score": item.get("score"),
                "scientificName": sp.get("scientificName"),
                "scientificNameWithoutAuthor": sp.get("scientificNameWithoutAuthor"),
                "family": pick_family(item),
                "commonNames": pick_common_names(item),
            }
        )

    top1 = simplified_top[0]["score"] if len(simplified_top) > 0 else None
    top2 = simplified_top[1]["score"] if len(simplified_top) > 1 else None
    gap = (top1 - top2) if (top1 is not None and top2 is not None) else None

    level = "alacsony"
    if top1 is not None:
        if top1 >= 0.7:
            level = "magas"
        elif top1 >= 0.4:
            level = "közepes"

    best_match = out.get("bestMatch")
    if not best_match and simplified_top:
        best_match = simplified_top[0].get("scientificName")

    return JSONResponse(
        {
            "bestMatch": best_match,
            "confidence": {
                "top1_score": top1,
                "level": level,
                "top1_top2_gap": gap,
            },
            "topMatches": simplified_top,
            "meta": {
                "organs": req.organs,
            },
            "raw": out,
        }
    )

