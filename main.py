from __future__ import annotations

import base64
import os
import time
from typing import Any, Dict, List, Optional, Literal

import requests
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


APP_VERSION = "2.0.0"

# ---------------------------
# Config (ENV)
# ---------------------------
PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_PROJECT = os.getenv("PLANTNET_PROJECT", "k-middle-europe").strip()
PLANTNET_BASE_URL = os.getenv("PLANTNET_BASE_URL", "https://my-api.plantnet.org").strip().rstrip("/")

# PlantNet endpoint is /v2/identify/{project}
def plantnet_identify_url(project: str) -> str:
    return f"{PLANTNET_BASE_URL}/v2/identify/{project}"


# ---------------------------
# Minimal "field weed" knowledge base (editable)
# You can expand this list later.
# If not found -> we keep it, but mark as "valószínű nem gyom" in gpt_friendly.
# ---------------------------
FIELD_WEEDS: Dict[str, Dict[str, Any]] = {
    "Capsella bursa-pastoris": {"hu_name": "pásztortáska", "group": "broadleaf", "crop_tags": ["wheat", "rape", "general"]},
    "Papaver rhoeas": {"hu_name": "pipacs", "group": "broadleaf", "crop_tags": ["wheat", "general"]},
    "Veronica persica": {"hu_name": "perzsa veronika", "group": "broadleaf", "crop_tags": ["wheat", "rape", "general"]},
    "Galium aparine": {"hu_name": "ragadós galaj", "group": "broadleaf", "crop_tags": ["wheat", "rape", "general"]},
    "Chenopodium album": {"hu_name": "fehér libatop", "group": "broadleaf", "crop_tags": ["maize", "sunflower", "soy", "beet", "general"]},
    "Conyza canadensis": {"hu_name": "kanadai betyárkóró", "group": "broadleaf", "crop_tags": ["general"]},
    "Poa annua": {"hu_name": "egyéves perje", "group": "grass", "crop_tags": ["wheat", "general"]},
    "Apera spica-venti": {"hu_name": "szélfű", "group": "grass", "crop_tags": ["wheat", "general"]},
    "Alopecurus myosuroides": {"hu_name": "egérárpa", "group": "grass", "crop_tags": ["wheat", "general"]},
}

CROPS_LATIN = {
    "Triticum aestivum",
    "Zea mays",
    "Brassica napus",
    "Helianthus annuus",
    "Glycine max",
    "Beta vulgaris",
}


# ---------------------------
# Models
# ---------------------------
Mode = Literal["weed", "disease", "pest", "crop", "auto"]

class DiagnoseJSONIn(BaseModel):
    image_b64: str = Field(..., description="Base64 image. May be data URL: data:image/jpeg;base64,...")
    mode: Mode = "weed"
    crop: Optional[Literal["wheat", "rape", "maize", "sunflower", "soy", "beet"]] = None
    note: Optional[str] = None
    debug: bool = False
    project: Optional[str] = None
    top_k: int = 10


# ---------------------------
# App
# ---------------------------
app = FastAPI(title="Plant Diagnosis API", version=APP_VERSION)


@app.get("/")
def root():
    return {
        "name": "plant-diagnosis",
        "version": APP_VERSION,
        "status": "ok",
        "endpoints": ["/health", "/docs", "/openapi.json", "/v1/diagnose", "/v1/diagnose_json"],
    }


@app.get("/health")
def health():
    return {"status": "ok", "ts": int(time.time())}


# ---------------------------
# Helpers
# ---------------------------
def _decode_data_url_or_b64(image_b64: str) -> bytes:
    s = image_b64.strip()
    if "base64," in s:
        s = s.split("base64,", 1)[1]
    try:
        return base64.b64decode(s, validate=True)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid base64 in image_b64: {e}")


def call_plantnet(image_bytes: bytes, project: str, top_k: int) -> Dict[str, Any]:
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="Server misconfigured: PLANTNET_API_KEY is missing")

    url = plantnet_identify_url(project)
    params = {
        "api-key": PLANTNET_API_KEY,
        # You can add more PlantNet params here later if needed
    }

    files = {
        # PlantNet expects multipart field name "images"
        "images": ("image.jpg", image_bytes, "image/jpeg"),
    }

    # PlantNet may accept organ hints; we omit for now for robustness
    try:
        r = requests.post(url, params=params, files=files, timeout=30)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"PlantNet request failed: {e}")

    if r.status_code >= 400:
        # Keep PlantNet message for debugging
        raise HTTPException(status_code=502, detail=f"PlantNet HTTP {r.status_code}: {r.text[:500]}")

    try:
        data = r.json()
    except Exception:
        raise HTTPException(status_code=502, detail="PlantNet returned non-JSON response")

    return data


def simplify_plantnet_response(raw: Dict[str, Any], top_k: int) -> Dict[str, Any]:
    results = raw.get("results") or []
    candidates = []
    for it in results[: max(top_k, 1)]:
        sp = (it.get("species") or {})
        sci = sp.get("scientificNameWithoutAuthor") or sp.get("scientificName") or "unknown"
        score = it.get("score", 0.0)
        common = sp.get("commonNames") or []
        candidates.append(
            {
                "scientific_name": sci,
                "confidence": float(score) if score is not None else 0.0,
                "common_names": common[:10],
            }
        )
    return {
        "engine": "plantnet",
        "project": raw.get("query", {}).get("project") or None,
        "top_k": top_k,
        "candidates": candidates,
    }


def classify_confidence(score: float) -> str:
    if score >= 0.70:
        return "high"
    if score >= 0.45:
        return "medium"
    return "low"


def weed_filter(candidates: List[Dict[str, Any]], crop: Optional[str]) -> Dict[str, Any]:
    kept = []
    dropped = []

    for c in candidates:
        sci = c.get("scientific_name", "")
        # Hard crop drop (if PlantNet returns the crop itself, not a weed)
        if sci in CROPS_LATIN:
            c2 = dict(c)
            c2["reason"] = "crop_species"
            dropped.append(c2)
            continue

        meta = FIELD_WEEDS.get(sci)
        c2 = dict(c)

        if meta:
            c2["hu_name"] = meta.get("hu_name")
            c2["group"] = meta.get("group")
            c2["crop_tags"] = meta.get("crop_tags", ["general"])
            # Crop-aware filtering: if crop specified and not matching tags, mark but keep (don’t hard drop)
            if crop and crop not in c2["crop_tags"] and "general" not in c2["crop_tags"]:
                c2["flag"] = "valószínű nem tipikus ebben a kultúrában"
            kept.append(c2)
        else:
            # Unknown to our field-weed DB: keep but mark as "probably not weed"
            c2["hu_name"] = None
            c2["group"] = None
            c2["crop_tags"] = ["unknown"]
            c2["flag"] = "valószínű nem gyom / nem a szántóföldi listában"
            kept.append(c2)

    top = kept[0] if kept else None
    top_score = float(top.get("confidence", 0.0)) if top else 0.0
    return {
        "kept": kept,
        "dropped": dropped,
        "top": top,
        "confidence_level": classify_confidence(top_score),
    }


def build_response(
    mode: Mode,
    crop: Optional[str],
    note: Optional[str],
    project: str,
    top_k: int,
    plantnet_block: Dict[str, Any],
    weed_pack: Dict[str, Any],
    debug: bool,
    input_type: str,
) -> JSONResponse:
    top = weed_pack.get("top") or {}
    gpt_friendly = {
        "top_species": top.get("scientific_name"),
        "top_hu_name": top.get("hu_name") or "magyar név nincs a szótárban",
        "confidence": top.get("confidence", 0.0),
        "confidence_level": weed_pack.get("confidence_level", "low"),
        "filtered_candidates": (weed_pack.get("kept") or [])[:5],
        "note": "PlantNet jelöltek + szántóföldi gyomlistás (crop-aware) szűrés.",
    }
    if debug:
        gpt_friendly["dropped_preview"] = (weed_pack.get("dropped") or [])[:10]

    return JSONResponse(
        {
            "ok": True,
            "request": {
                "mode": mode,
                "crop": crop,
                "note": note,
                "project": project,
                "top_k": top_k,
                "input": input_type,
            },
            "plantnet": plantnet_block,
            "gpt_friendly": gpt_friendly,
        }
    )


# ---------------------------
# Main endpoints (EN)
# ---------------------------
@app.post("/v1/diagnose", tags=["diagnosis"])
async def diagnose_multipart(
    image: UploadFile = File(...),
    mode: Mode = Form("weed"),
    crop: Optional[Literal["wheat", "rape", "maize", "sunflower", "soy", "beet"]] = Form(None),
    note: Optional[str] = Form(None),
    debug: bool = Form(False),
    project: Optional[str] = Form(None),
    top_k: int = Form(10),
):
    project_eff = (project or PLANTNET_PROJECT).strip()
    img_bytes = await image.read()

    raw = call_plantnet(img_bytes, project=project_eff, top_k=top_k)
    plantnet_block = simplify_plantnet_response(raw, top_k=top_k)

    weed_pack = weed_filter(plantnet_block["candidates"], crop=crop)
    return build_response(
        mode=mode,
        crop=crop,
        note=note,
        project=project_eff,
        top_k=top_k,
        plantnet_block=plantnet_block,
        weed_pack=weed_pack,
        debug=debug,
        input_type="multipart",
    )


@app.post("/v1/diagnose_json", tags=["diagnosis"])
async def diagnose_json(payload: DiagnoseJSONIn = Body(...)):
    project_eff = (payload.project or PLANTNET_PROJECT).strip()
    img_bytes = _decode_data_url_or_b64(payload.image_b64)

    raw = call_plantnet(img_bytes, project=project_eff, top_k=payload.top_k)
    plantnet_block = simplify_plantnet_response(raw, top_k=payload.top_k)

    weed_pack = weed_filter(plantnet_block["candidates"], crop=payload.crop)
    return build_response(
        mode=payload.mode,
        crop=payload.crop,
        note=payload.note,
        project=project_eff,
        top_k=payload.top_k,
        plantnet_block=plantnet_block,
        weed_pack=weed_pack,
        debug=payload.debug,
        input_type="json_base64",
    )


# ---------------------------
# Hungarian aliases (HU) - same handlers
# ---------------------------
@app.post("/v1/diagnosztika", tags=["diagnosis"])
async def diagnosztika_alias(
    image: UploadFile = File(...),
    mode: Mode = Form("weed"),
    crop: Optional[Literal["wheat", "rape", "maize", "sunflower", "soy", "beet"]] = Form(None),
    note: Optional[str] = Form(None),
    debug: bool = Form(False),
    project: Optional[str] = Form(None),
    top_k: int = Form(10),
):
    return await diagnose_multipart(
        image=image, mode=mode, crop=crop, note=note, debug=debug, project=project, top_k=top_k
    )


@app.post("/v1/diagnosztika_json", tags=["diagnosis"])
async def diagnosztika_json_alias(payload: DiagnoseJSONIn = Body(...)):
    return await diagnose_json(payload=payload)
