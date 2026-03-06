import os
import json
import base64
import time
from typing import Optional, List, Dict, Any, Literal

import requests
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse


APP_VERSION = "3.0.0"

# -----------------------------
# Environment
# -----------------------------
PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_BASE_URL = os.getenv("PLANTNET_BASE_URL", "https://my-api.plantnet.org").strip().rstrip("/")
PLANTNET_PROJECT_DEFAULT = os.getenv("PLANTNET_PROJECT", "k-middle-europe").strip()
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").strip()

Mode = Literal["weed", "disease", "pest", "auto"]

# -----------------------------
# Gyom seed lista (bővíthető)
# -----------------------------
FIELD_WEEDS: Dict[str, Dict[str, Any]] = {
    "Capsella bursa-pastoris": {"hu_name": "pásztortáska", "group": "broadleaf", "crop_tags": ["wheat", "rape", "general"]},
    "Papaver rhoeas": {"hu_name": "pipacs", "group": "broadleaf", "crop_tags": ["wheat", "general"]},
    "Veronica persica": {"hu_name": "perzsa veronika", "group": "broadleaf", "crop_tags": ["wheat", "rape", "general"]},
    "Galium aparine": {"hu_name": "ragadós galaj", "group": "broadleaf", "crop_tags": ["wheat", "rape", "general"]},
    "Chenopodium album": {"hu_name": "fehér libatop", "group": "broadleaf", "crop_tags": ["maize", "sunflower", "soy", "beet", "general"]},
    "Conyza canadensis": {"hu_name": "kanadai betyárkóró", "group": "broadleaf", "crop_tags": ["general"]},
    "Poa annua": {"hu_name": "egynyári perje", "group": "grass", "crop_tags": ["wheat", "general"]},
    "Apera spica-venti": {"hu_name": "nagy széltippan", "group": "grass", "crop_tags": ["wheat", "general"]},
    "Alopecurus myosuroides": {"hu_name": "nagy rókafarkfű", "group": "grass", "crop_tags": ["wheat", "general"]},
    "Stellaria media": {"hu_name": "tyúkhúr", "group": "broadleaf", "crop_tags": ["wheat", "rape", "general"]},
    "Lamium purpureum": {"hu_name": "piros árvacsalán", "group": "broadleaf", "crop_tags": ["wheat", "rape", "general"]},
    "Lamium amplexicaule": {"hu_name": "szárölelő árvacsalán", "group": "broadleaf", "crop_tags": ["wheat", "rape", "general"]},
    "Tripleurospermum inodorum": {"hu_name": "ebszékfű", "group": "broadleaf", "crop_tags": ["wheat", "rape", "general"]},
}

# -----------------------------
# App
# -----------------------------
app = FastAPI(title="Plant Diagnosis API", version=APP_VERSION, openapi_version="3.1.0")

origins = ["*"] if ALLOWED_ORIGINS == "*" else [o.strip() for o in ALLOWED_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------
# Helpers
# -----------------------------
def _strip_data_url_prefix(b64: str) -> str:
    if not b64:
        return b64
    b64 = b64.strip()
    if b64.startswith("data:"):
        parts = b64.split(",", 1)
        if len(parts) == 2:
            return parts[1].strip()
    return b64


def _confidence_level(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.45:
        return "medium"
    return "low"


def _plantnet_identify(image_bytes: bytes, project: str, top_k: int, organs: str = "leaf", debug: bool = False) -> Dict[str, Any]:
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="Missing PLANTNET_API_KEY env var.")

    url = f"{PLANTNET_BASE_URL}/v2/identify/{project}"
    params = {"api-key": PLANTNET_API_KEY}
    files = {"images": ("image.jpg", image_bytes, "image/jpeg")}
    data = {"organs": organs}

    try:
        resp = requests.post(url, params=params, files=files, data=data, timeout=60)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"PlantNet request failed: {e}")

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"PlantNet HTTP {resp.status_code}. url={resp.url}. body={resp.text[:800]}"
        )

    raw = resp.json()

    results = raw.get("results", []) or []
    candidates: List[Dict[str, Any]] = []
    for r in results[: max(1, min(top_k, 10))]:
        sp = r.get("species") or {}
        sci = sp.get("scientificNameWithoutAuthor") or sp.get("scientificName") or "unknown"
        candidates.append(
            {
                "scientific_name": sci,
                "confidence": float(r.get("score", 0.0)),
                "common_names": sp.get("commonNames") or [],
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


def _weed_filter(candidates: List[Dict[str, Any]], crop: Optional[str]) -> (List[Dict[str, Any]], List[Dict[str, Any]]):
    kept: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    crop_tag = (crop or "general").strip().lower()

    for c in candidates:
        sci = c.get("scientific_name", "")
        meta = FIELD_WEEDS.get(sci)
        if meta:
            tags = [t.lower() for t in meta.get("crop_tags", [])]
            enriched = dict(c)
            enriched["hu_name"] = meta.get("hu_name")
            enriched["group"] = meta.get("group")
            enriched["crop_tags"] = meta.get("crop_tags")
            if crop and crop_tag not in tags and "general" not in tags:
                enriched["flag"] = "valószínű nem tipikus ebben a kultúrában"
            kept.append(enriched)
        else:
            enriched = dict(c)
            enriched["hu_name"] = None
            enriched["group"] = None
            enriched["crop_tags"] = ["unknown"]
            enriched["flag"] = "valószínű nem gyom / nem a szántóföldi listában"
            kept.append(enriched)

    return kept, dropped


def _build_gpt_friendly(mode: str, crop: Optional[str], result_block: Dict[str, Any]) -> Dict[str, Any]:
    candidates = result_block.get("candidates") or []

    if mode == "weed":
        filtered, dropped = _weed_filter(candidates, crop)
    else:
        filtered, dropped = candidates, []

    top = filtered[0] if filtered else {"scientific_name": "unknown", "confidence": 0.0}
    score = float(top.get("confidence", 0.0))

    return {
        "top_species": top.get("scientific_name"),
        "top_hu_name": top.get("hu_name") or "magyar név nincs a szótárban",
        "confidence": score,
        "confidence_level": _confidence_level(score),
        "filtered_candidates": filtered[: result_block.get("top_k", 5)],
        "note": "PlantNet találatok GPT-barát, crop-aware szűréssel.",
        "dropped_preview": dropped[:5],
        "needs_more_photos": score < 0.45,
        "photo_hint": (
            "Alacsony pontszám. Kérlek küldj új képet: teljes növény + közelkép a diagnosztikai bélyegekről."
            if score < 0.45 else None
        ),
    }


def _run_pipeline(image_bytes: bytes, mode: str, crop: Optional[str], project: str, top_k: int, debug: bool) -> Dict[str, Any]:
    # Jelenleg mindhárom mód ugyanarra a PlantNet motorra megy rá.
    # Később disease/pest külön logikára váltható.
    result_block = _plantnet_identify(
        image_bytes=image_bytes,
        project=project,
        top_k=top_k,
        organs="leaf",
        debug=debug,
    )
    gpt_friendly = _build_gpt_friendly(mode=mode, crop=crop, result_block=result_block)
    return {
        "plantnet": {k: v for k, v in result_block.items() if k != "raw"},
        "gpt_friendly": gpt_friendly,
        "raw": result_block.get("raw") if debug else None,
    }


# -----------------------------
# Routes
# -----------------------------
@app.get("/")
def root():
    return {
        "name": "plant-diagnosis",
        "version": APP_VERSION,
        "status": "ok",
        "endpoints": ["/health", "/v1/diagnose", "/v1/diagnose_json", "/docs", "/openapi.json"],
    }


@app.get("/health")
def health():
    return {"status": "ok", "version": APP_VERSION, "default_project": PLANTNET_PROJECT_DEFAULT}


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
    payload = _run_pipeline(
        image_bytes=image_bytes,
        mode=mode,
        crop=crop,
        project=project.strip(),
        top_k=int(top_k),
        debug=debug,
    )

    return {
        "ok": True,
        "request": {
            "mode": mode,
            "crop": crop,
            "project": project,
            "top_k": top_k,
            "filename": image.filename,
            "content_type": image.content_type,
            "note": note,
        },
        "plantnet": payload["plantnet"],
        "gpt_friendly": payload["gpt_friendly"],
        "raw": payload["raw"],
    }


@app.post("/v1/diagnose_json")
async def diagnose_json(request: Request):
    body = await request.body()
    ct = (request.headers.get("content-type") or "").lower()

    data = None
    if "application/json" in ct:
        try:
            data = await request.json()
        except Exception:
            pass

    if data is None:
        try:
            data = json.loads(body.decode("utf-8", errors="replace"))
        except Exception:
            snippet = body[:200].decode("utf-8", errors="replace")
            raise HTTPException(
                status_code=400,
                detail=f"Bad Request: cannot parse JSON. content-type={ct}. body_head={snippet!r}"
            )

    image_b64 = (data.get("image_b64") or "").strip()
    if not image_b64:
        raise HTTPException(status_code=400, detail="Bad Request: missing image_b64")

    mode = (data.get("mode") or "weed").strip().lower()
    crop = data.get("crop")
    project = (data.get("project") or PLANTNET_PROJECT_DEFAULT).strip()
    top_k = int(data.get("top_k") or 5)
    note = data.get("note")
    debug = bool(data.get("debug") or False)

    try:
        image_bytes = base64.b64decode(_strip_data_url_prefix(image_b64), validate=False)
    except Exception:
        raise HTTPException(status_code=400, detail="Bad Request: invalid base64 in image_b64")

    if not image_bytes:
        raise HTTPException(status_code=400, detail="Bad Request: decoded image bytes are empty")

    payload = _run_pipeline(
        image_bytes=image_bytes,
        mode=mode,
        crop=crop,
        project=project,
        top_k=top_k,
        debug=debug,
    )

    return {
        "ok": True,
        "request": {
            "mode": mode,
            "crop": crop,
            "project": project,
            "top_k": top_k,
            "note": note,
            "input": "json_base64",
        },
        "plantnet": payload["plantnet"],
        "gpt_friendly": payload["gpt_friendly"],
        "raw": payload["raw"],
    }
