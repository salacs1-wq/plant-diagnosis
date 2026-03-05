import os
import base64
import json
from typing import Optional, List, Dict, Any, Tuple

import requests
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


APP_VERSION = "2.0.0"

# -----------------------------
# Environment
# -----------------------------
PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_BASE_URL = os.getenv("PLANTNET_BASE_URL", "https://my-api.plantnet.org").strip().rstrip("/")
PLANTNET_PROJECT_DEFAULT = os.getenv("PLANTNET_PROJECT", "k-middle-europe").strip()
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").strip()

# -----------------------------
# Minimal "field weed" seed list (bővíthető)
# -----------------------------
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

# -----------------------------
# FastAPI app
# -----------------------------
app = FastAPI(title="Plant Diagnosis API", version=APP_VERSION, openapi_version="3.1.0")

# CORS
origins = ["*"] if ALLOWED_ORIGINS == "*" else [o.strip() for o in ALLOWED_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------
# Pydantic models
# -----------------------------
class DiagnoseJsonRequest(BaseModel):
    image_b64: str = Field(..., description="Base64 image. May include data URL prefix: data:image/jpeg;base64,...")
    mode: str = Field(default="weed", description="weed | auto | disease | pest (jelenleg weed/auto ugyanaz a PlantNet-ben)")
    crop: Optional[str] = Field(default=None, description="Optional crop tag (e.g. wheat, rape, maize...)")
    project: str = Field(default=PLANTNET_PROJECT_DEFAULT, description="PlantNet project (default k-middle-europe)")
    top_k: int = Field(default=5, ge=1, le=10)
    note: Optional[str] = None
    debug: bool = False


# -----------------------------
# Helpers
# -----------------------------
def _strip_data_url_prefix(b64: str) -> str:
    """
    Accept:
      - raw base64
      - data:image/jpeg;base64,XXXX
    Return raw base64 only.
    """
    if not b64:
        return b64
    b64 = b64.strip()
    if b64.startswith("data:"):
        parts = b64.split(",", 1)
        if len(parts) == 2:
            return parts[1].strip()
    return b64


def _plantnet_identify(image_bytes: bytes, project: str, top_k: int, organs: str = "leaf", debug: bool = False) -> Dict[str, Any]:
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="Missing PLANTNET_API_KEY env var.")

    # PlantNet endpoint (v2)
    # IMPORTANT: PLANTNET_BASE_URL must be like https://my-api.plantnet.org (no /v2)
    url = f"{PLANTNET_BASE_URL}/v2/identify/{project}"

    params = {
        "api-key": PLANTNET_API_KEY,
        # include-related images is optional; keep simple
    }

    # PlantNet expects "images" field and "organs"
    files = {
        "images": ("image.jpg", image_bytes, "image/jpeg"),
    }
    data = {
        "organs": organs,  # leaf is default for seedlings/field weeds
    }

    try:
        resp = requests.post(url, params=params, files=files, data=data, timeout=60)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"PlantNet request failed: {e}")

    if resp.status_code != 200:
        # Return readable error
        try:
            payload = resp.json()
        except Exception:
            payload = {"text": resp.text[:4000]}
        raise HTTPException(status_code=502, detail=f"PlantNet HTTP {resp.status_code}: {json.dumps(payload, ensure_ascii=False)}")

    raw = resp.json()

    # Normalize to candidates list
    candidates: List[Dict[str, Any]] = []
    results = raw.get("results", []) or []
    for r in results[: max(0, min(top_k, 10))]:
        sp = r.get("species") or {}
        sci = sp.get("scientificNameWithoutAuthor") or sp.get("scientificName") or "unknown"
        common_names = sp.get("commonNames") or []
        score = r.get("score", 0.0)
        candidates.append(
            {
                "scientific_name": sci,
                "confidence": float(score),
                "common_names": common_names,
            }
        )

    return {
        "engine": "plantnet",
        "project": project,
        "top_k": top_k,
        "organs": organs,
        "candidates": candidates,
        "raw": raw if debug else None,
    }


def _weed_filter(candidates: List[Dict[str, Any]], crop: Optional[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Keep candidates that are in FIELD_WEEDS when mode=weed.
    Others go to dropped_preview.
    If none match, keep original list but mark as "unknown in field list".
    """
    kept: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []

    crop_tag = (crop or "general").strip().lower()

    for c in candidates:
        sci = c.get("scientific_name", "")
        meta = FIELD_WEEDS.get(sci)
        if meta:
            # crop-aware filter: keep if crop matches or general
            tags = [t.lower() for t in (meta.get("crop_tags") or [])]
            if (crop_tag in tags) or ("general" in tags) or (not crop):
                enriched = dict(c)
                enriched["hu_name"] = meta.get("hu_name")
                enriched["group"] = meta.get("group")
                enriched["crop_tags"] = meta.get("crop_tags")
                kept.append(enriched)
            else:
                dropped.append(dict(c))
        else:
            dropped.append(dict(c))

    # If we dropped everything, keep original list (better UX), but still show note
    if not kept:
        kept = [dict(c) for c in candidates]

    return kept, dropped


def _confidence_level(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.45:
        return "medium"
    return "low"


def _build_gpt_friendly(mode: str, crop: Optional[str], plantnet_block: Dict[str, Any]) -> Dict[str, Any]:
    candidates = plantnet_block.get("candidates") or []
    top = candidates[0] if candidates else {"scientific_name": "unknown", "confidence": 0.0, "common_names": []}
    top_score = float(top.get("confidence", 0.0))

    filtered = candidates
    dropped_preview: List[Dict[str, Any]] = []
    note = ""

    if mode == "weed":
        filtered, dropped_preview = _weed_filter(candidates, crop)
        note = "PlantNet jelöltek szántóföldi gyom seed listával szűrve (crop-aware, ha meg van adva)."

    # pick top from filtered
    top_filtered = filtered[0] if filtered else top
    top_species = top_filtered.get("scientific_name", "unknown")
    hu_name = top_filtered.get("hu_name") or None

    return {
        "top_species": top_species,
        "top_hu_name": hu_name,
        "confidence": float(top_filtered.get("confidence", 0.0)),
        "confidence_level": _confidence_level(float(top_filtered.get("confidence", 0.0))),
        "filtered_candidates": filtered[: plantnet_block.get("top_k", 5)],
        "note": note,
        "dropped_preview": dropped_preview[:5],
        "needs_more_photos": top_score < 0.45,
        "photo_hint": (
            "Alacsony pontszám. Kérlek 1-2 új képet: teljes növény felülről + levélhüvely/nyelvecske közelről (egyszikű), "
            "vagy teljes növény + levélállás/szőrözöttség (kétszikű)."
            if top_score < 0.45
            else None
        ),
    }


# -----------------------------
# Routes
# -----------------------------
@app.get("/")
def root():
    return {"name": "növénydiagnózis", "version": APP_VERSION, "status": "ok", "endpoints": ["/health", "/v1/diagnose", "/v1/diagnose_json", "/docs", "/openapi.json"]}


@app.get("/health")
def health():
    return {"status": "ok", "version": APP_VERSION}


@app.post("/v1/diagnose")
async def diagnose_multipart(
    image: UploadFile = File(...),
    mode: str = Form("weed"),
    crop: Optional[str] = Form(None),
    project: str = Form(PLANTNET_PROJECT_DEFAULT),
    top_k: int = Form(5),
    note: Optional[str] = Form(None),
    debug: bool = Form(False),
):
    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty image.")

    mode = (mode or "weed").strip().lower()
    project = (project or PLANTNET_PROJECT_DEFAULT).strip()
    top_k = int(top_k)

    # For now, PlantNet: always leaf (seedlings). Later can be smarter.
    plantnet = _plantnet_identify(image_bytes=image_bytes, project=project, top_k=top_k, organs="leaf", debug=debug)

    gpt_friendly = _build_gpt_friendly(mode=mode, crop=crop, plantnet_block=plantnet)

    return {
        "ok": True,
        "request": {
            "mode": mode,
            "crop": crop,
            "filename": image.filename,
            "content_type": image.content_type,
            "note": note,
        },
        "plantnet": {k: v for k, v in plantnet.items() if k != "raw"},
        "gpt_friendly": gpt_friendly,
        "raw": plantnet.get("raw") if debug else None,
    }


@app.post("/v1/diagnose_json")
def diagnose_json(payload: DiagnoseJsonRequest):
    mode = (payload.mode or "weed").strip().lower()
    project = (payload.project or PLANTNET_PROJECT_DEFAULT).strip()
    top_k = int(payload.top_k)

    b64 = _strip_data_url_prefix(payload.image_b64)
    try:
        image_bytes = base64.b64decode(b64, validate=False)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 image_b64.")

    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty decoded image bytes.")

    plantnet = _plantnet_identify(image_bytes=image_bytes, project=project, top_k=top_k, organs="leaf", debug=payload.debug)

    gpt_friendly = _build_gpt_friendly(mode=mode, crop=payload.crop, plantnet_block=plantnet)

    return {
        "ok": True,
        "request": {
            "mode": mode,
            "crop": payload.crop,
            "project": project,
            "top_k": top_k,
            "note": payload.note,
        },
        "plantnet": {k: v for k, v in plantnet.items() if k != "raw"},
        "gpt_friendly": gpt_friendly,
        "raw": plantnet.get("raw") if payload.debug else None,
    }


# Hungarian aliases (so you can keep "diagnosztika" in your GPT instructions if needed)
@app.post("/v1/diagnosztika")
async def diagnosztika_alias(
    image: UploadFile = File(...),
    mode: str = Form("weed"),
    crop: Optional[str] = Form(None),
    project: str = Form(PLANTNET_PROJECT_DEFAULT),
    top_k: int = Form(5),
    note: Optional[str] = Form(None),
    debug: bool = Form(False),
):
    return await diagnose_multipart(image=image, mode=mode, crop=crop, project=project, top_k=top_k, note=note, debug=debug)


@app.post("/v1/diagnosztika_json")
def diagnosztika_json_alias(payload: DiagnoseJsonRequest):
    return diagnose_json(payload)
