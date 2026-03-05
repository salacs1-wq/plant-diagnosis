from __future__ import annotations

import io
import os
import time
from typing import Any, Dict, List, Literal, Optional

import requests
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

try:
    from PIL import Image
except Exception:
    Image = None  # pillow optional


Mode = Literal["weed", "disease", "pest", "crop", "auto"]

APP_NAME = "plant-diagnosis"
APP_VERSION = "1.0.0"

app = FastAPI(title="Plant Diagnosis API", version=APP_VERSION)

# CORS
allowed_origins = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in allowed_origins] if allowed_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["meta"])
def root() -> Dict[str, Any]:
    return {
        "name": APP_NAME,
        "version": APP_VERSION,
        "status": "ok",
        "endpoints": ["/health", "/v1/diagnose", "/docs", "/openapi.json"],
    }


@app.get("/health", tags=["meta"])
def health() -> Dict[str, Any]:
    return {"status": "ok", "ts": int(time.time())}


def _image_debug_info(image_bytes: bytes) -> Dict[str, Any]:
    info: Dict[str, Any] = {"bytes": len(image_bytes)}
    if Image is None:
        info["pil"] = "not_installed"
        return info
    try:
        img = Image.open(io.BytesIO(image_bytes))
        info.update(
            {
                "pil": "ok",
                "format": img.format,
                "size": {"width": img.size[0], "height": img.size[1]},
                "mode": img.mode,
            }
        )
    except Exception as e:
        info["pil"] = "error"
        info["pil_error"] = str(e)
    return info


def call_plantnet(image_bytes: bytes) -> Dict[str, Any]:
    api_key = os.getenv("PLANTNET_API_KEY", "").strip()
    base_url = os.getenv("PLANTNET_BASE_URL", "https://my-api.plantnet.org").strip().rstrip("/")
    project = os.getenv("PLANTNET_PROJECT", "k-middle-europe").strip()

    if not api_key:
        raise HTTPException(status_code=500, detail="PLANTNET_API_KEY nincs beállítva a Render env-ben.")

    # API kulcs query paraméterben!
    url = f"{base_url}/v2/identify/{project}?api-key={api_key}"

    files = {
        "images": ("image.jpg", image_bytes, "image/jpeg"),
    }

    try:
        r = requests.post(url, files=files, timeout=60)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"PlantNet hívási hiba: {e}")

    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"PlantNet HTTP {r.status_code}: {r.text[:500]}")

    try:
        return r.json()
    except Exception:
        raise HTTPException(status_code=502, detail="PlantNet válasz nem JSON.")


def simplify_plantnet_response(raw: Dict[str, Any], top_k: int = 5) -> Dict[str, Any]:
    results = raw.get("results", []) or []
    simple: List[Dict[str, Any]] = []

    for item in results[:top_k]:
        species = item.get("species", {}) or {}
        sci = species.get("scientificNameWithoutAuthor") or species.get("scientificName") or "unknown"
        common = species.get("commonNames") or []
        simple.append(
            {
                "scientific_name": sci,
                "confidence": item.get("score", None),
                "common_names": common[:5],
            }
        )

    return {
        "engine": "plantnet",
        "project": raw.get("project") or os.getenv("PLANTNET_PROJECT", "k-middle-europe"),
        "top_k": top_k,
        "candidates": simple,
    }


@app.post("/v1/diagnose", tags=["diagnosis"])
async def diagnose(
    mode: Mode = Form("auto"),
    image: UploadFile = File(...),
    note: Optional[str] = Form(None),
    debug: bool = Form(False),
) -> JSONResponse:
    if mode not in ("weed", "disease", "pest", "crop", "auto"):
        raise HTTPException(status_code=400, detail="Érvénytelen mode.")

    image_bytes = await image.read()
    if not image_bytes or len(image_bytes) < 50:
        raise HTTPException(status_code=400, detail="Üres / túl kicsi kép.")

    raw = call_plantnet(image_bytes)
    result = simplify_plantnet_response(raw, top_k=5)

    payload: Dict[str, Any] = {
        "ok": True,
        "request": {
            "mode": mode,
            "filename": image.filename,
            "content_type": image.content_type,
            "note": note,
        },
        "result": result,
    }

    if debug:
        payload["debug"] = _image_debug_info(image_bytes)

    return JSONResponse(payload)
