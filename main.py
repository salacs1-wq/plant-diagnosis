import os
import io
from typing import Optional, List, Dict, Any

import httpx
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
from PIL import Image

app = FastAPI(title="Plant Diagnosis API", version="1.1.0")

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_URL = "https://my-api.plantnet.org/v2/identify/k-middle-europe"  # PlantNet endpoint

@app.get("/")
def root():
    return {"ok": True, "service": "plant-diagnosis-1", "hint": "Use /health or POST /diagnose"}

@app.get("/health")
def health():
    return {"ok": True, "plantnet_key_set": bool(PLANTNET_API_KEY)}

def _basic_image_info(content: bytes) -> Dict[str, Any]:
    img = Image.open(io.BytesIO(content))
    w, h = img.size
    return {"width": w, "height": h}

@app.post("/diagnose")
async def diagnose(
    file: UploadFile = File(...),
    caseType: str = Form("weed"),                 # weed|disease|pest|symptom (most a PlantNet főleg növény)
    project: str = Form("k-middle-europe"),       # fix default
    mode: str = Form("expert"),                   # expert|fast (későbbre)
    organs: Optional[str] = Form(None),           # pl: "leaf,flower" (opcionális)
):
    content = await file.read()

    # Képként olvasható-e?
    try:
        info = _basic_image_info(content)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "A feltöltött fájl nem olvasható képként."})

    received = {
        "filename": file.filename,
        "content_type": file.content_type,
        "bytes": len(content),
        **info
    }

    # Ha nincs kulcs, maradjon stub, de legalább stabilan működjön
    if not PLANTNET_API_KEY:
        return {
            "ok": True,
            "received": received,
            "params": {"caseType": caseType, "project": project, "mode": mode, "organs": organs},
            "result": {"note": "PlantNet API kulcs nincs beállítva (PLANTNET_API_KEY)."}
        }

    # PlantNet hívás
    params = {
        "api-key": PLANTNET_API_KEY
    }
    if organs:
        # PlantNet több organs paramot is elfogad, egyszerűen továbbadjuk
        params["organs"] = organs

    files = {
        "images": (file.filename or "image.jpg", content, file.content_type or "image/jpeg")
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(PLANTNET_URL, params=params, files=files)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        return JSONResponse(
            status_code=502,
            content={"error": "PlantNet HTTP hiba", "status_code": e.response.status_code, "detail": e.response.text},
        )
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": "PlantNet hívás sikertelen", "detail": str(e)})

    # Egységesített Top5
    results = []
    for item in (data.get("results") or [])[:5]:
        score = item.get("score")
        species = (item.get("species") or {})
        sci = species.get("scientificNameWithoutAuthor") or species.get("scientificName")
        common = species.get("commonNames") or []
        results.append({
            "score": score,
            "scientific_name": sci,
            "common_names": common[:5],
        })

    return {
        "ok": True,
        "received": received,
        "params": {"caseType": caseType, "project": project, "mode": mode, "organs": organs},
        "top5": results,
        "raw_meta": {
            "is_plantnet": True,
            "remaining_requests": data.get("remainingIdentificationRequests"),
        },
    }
