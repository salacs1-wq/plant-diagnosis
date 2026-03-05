from __future__ import annotations

import os
import io
import time
from typing import Any, Dict, Optional, List, Literal

import requests
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import requests

def run_inference(mode: Mode, image_bytes: bytes) -> Dict[str, Any]:
    api_key = os.getenv("PLANTNET_API_KEY", "").strip()
    base_url = os.getenv("PLANTNET_BASE_URL", "https://my-api.plantnet.org").strip().rstrip("/")
    project = os.getenv("PLANTNET_PROJECT", "k-middle-europe").strip()

    if not api_key:
        return {
            "mode": mode,
            "engine": "plantnet",
            "error": "PLANTNET_API_KEY nincs beállítva",
            "candidates": [],
        }

    url = f"{base_url}/v2/identify/{project}"
    files = {"images": ("image.jpg", image_bytes, "image/jpeg")}
    data = {"api-key": api_key}

    r = requests.post(url, files=files, data=data, timeout=60)
    if r.status_code >= 400:
        return {
            "mode": mode,
            "engine": "plantnet",
            "error": f"HTTP {r.status_code}: {r.text[:300]}",
            "candidates": [],
        }

    raw = r.json()
    results = raw.get("results", []) or []

    cands = []
    for item in results[:5]:
        species = item.get("species", {}) or {}
        cands.append({
            "label": species.get("scientificNameWithoutAuthor") or species.get("scientificName") or "unknown",
            "confidence": item.get("score", None),
            "common_names": (species.get("commonNames") or [])[:5],
        })

    return {"mode": mode, "engine": "plantnet", "candidates": cands}

try:
    from PIL import Image
except Exception:
    Image = None  # pillow optional, de ajánlott


Mode = Literal["weed", "disease", "pest", "crop", "auto"]

APP_NAME = "plant-diagnosis"
APP_VERSION = "1.0.0"

app = FastAPI(title="Plant Diagnosis API", version=APP_VERSION)

# CORS (GPT / web kliens miatt)
allowed_origins = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in allowed_origins] if allowed_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root() -> Dict[str, Any]:
    return {
        "name": APP_NAME,
        "version": APP_VERSION,
        "status": "ok",
        "endpoints": ["/health", "/v1/diagnose", "/docs", "/openapi.json"],
    }


@app.get("/health")
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

    # PlantNet Identify endpoint
    url = f"{base_url}/v2/identify/{project}"

    files = {
        # a PlantNet több képet is tud fogadni, mi most egyet küldünk
        "images": ("image.jpg", image_bytes, "image/jpeg"),
    }
    data = {
        "api-key": api_key,
        # opcionális: organs mező, ha akarod: "organs": "leaf"
        # opcionális: include-related-images stb.
    }

    try:
        r = requests.post(url, files=files, data=data, timeout=60)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"PlantNet hívási hiba: {e}")

    if r.status_code >= 400:
        # PlantNet hibaüzenet visszaadása debughoz
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
        score = item.get("score", None)

        sci = species.get("scientificNameWithoutAuthor") or species.get("scientificName") or "unknown"
        auth = species.get("scientificNameAuthorship")
        common = species.get("commonNames") or []
        family = (species.get("family") or {}).get("scientificNameWithoutAuthor") if isinstance(species.get("family"), dict) else None
        genus = (species.get("genus") or {}).get("scientificNameWithoutAuthor") if isinstance(species.get("genus"), dict) else None

        simple.append(
            {
                "scientific_name": sci,
                "authorship": auth,
                "common_names": common[:5],
                "family": family,
                "genus": genus,
                "confidence": score,
            }
        )

    return {
        "engine": "plantnet",
        "project": raw.get("project") or os.getenv("PLANTNET_PROJECT", "k-middle-europe"),
        "top_k": top_k,
        "candidates": simple,
    }


@app.post("/v1/diagnose")
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
