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
APP_VERSION = "1.1.0"

app = FastAPI(title="Plant Diagnosis API", version=APP_VERSION)
import base64
from fastapi import Body, HTTPException
from pydantic import BaseModel
from typing import Optional, Literal

class DiagnoseJSONIn(BaseModel):
    image_b64: str
    mode: Optional[Literal["weed", "disease", "pest", "crop", "auto"]] = "weed"
    crop: Optional[str] = None
    note: Optional[str] = None
    debug: Optional[bool] = False

@app.post("/v1/diagnosztika_json", tags=["diagnosis"])
@app.post("/v1/diagnose_json", tags=["diagnosis"])
async def diagnose_json(payload: DiagnoseJSONIn = Body(...)):
    image_b64 = payload.image_b64

    # data URL elfogadása (data:image/jpeg;base64,...)
    if "base64," in image_b64:
        image_b64 = image_b64.split("base64,", 1)[1]

    try:
        image_bytes = base64.b64decode(image_b64, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 in image_b64")

    # ---- IDE A SAJÁT MEGLÉVŐ LOGIKÁD HÍVÁSAI ----
    # pl.:
    # raw = call_plantnet(image_bytes, project="k-middle-europe", top_k=10)
    # plantnet = simplify_plantnet_response(raw, top_k=10)
    # weed_pack = filter_to_field_weeds(plantnet["candidates"], crop=payload.crop)
    # top = weed_pack["top"] or {}

    # Placeholder: cseréld a saját függvényeidre
    raw = call_plantnet(image_bytes)  # <-- NÁLAD EZ MÁR MEGVAN
    plantnet = simplify_plantnet_response(raw, top_k=10)  # <-- NÁLAD EZ MÁR MEGVAN
    weed_pack = filter_to_field_weeds(plantnet["candidates"], crop=payload.crop)  # <-- NÁLAD EZ MÁR MEGVAN
    top = weed_pack.get("top") or {}

    gpt_friendly = {
        "top_species": top.get("scientific_name"),
        "top_hu_name": top.get("hu_name"),
        "confidence": top.get("confidence"),
        "confidence_level": weed_pack.get("confidence_level", "low"),
        "filtered_candidates": (weed_pack.get("kept") or [])[:5],
        "note": "PlantNet jelöltek szántóföldi gyom adatbázissal szűrve (crop-aware).",
    }

    if payload.debug:
        gpt_friendly["dropped_preview"] = (weed_pack.get("dropped") or [])[:10]

    return {
        "ok": True,
        "request": {
            "mode": payload.mode,
            "crop": payload.crop,
            "note": payload.note,
            "input": "json_base64",
        },
        "plantnet": plantnet,
        "gpt_friendly": gpt_friendly,
    }
# -------------------------
# CORS
# -------------------------
allowed_origins = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in allowed_origins] if allowed_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------
# Szántóföldi gyom adatbázis (HU) – beágyazott, egyszerű verzió
# (Bővíthető később fájlból / DB-ből)
# -------------------------
# crop_tags: wheat, rape, maize, sunflower, soy, beet, general
# group: grass, broadleaf, sedge, horsetail
FIELD_WEEDS_HU: List[Dict[str, Any]] = [
    # EGYSZIKŰ / FŰFÉLÉK (kalászosokban kiemelten)
    {"latin": "Apera spica-venti", "hu": "nagy széltippan", "group": "grass", "crop_tags": ["wheat", "general"]},
    {"latin": "Alopecurus myosuroides", "hu": "nagy rókafarkfű", "group": "grass", "crop_tags": ["wheat", "general"]},
    {"latin": "Avena fatua", "hu": "héla zab", "group": "grass", "crop_tags": ["wheat", "general"]},
    {"latin": "Avena sterilis", "hu": "meddő zab", "group": "grass", "crop_tags": ["wheat", "general"]},
    {"latin": "Bromus sterilis", "hu": "meddő rozsnok", "group": "grass", "crop_tags": ["wheat", "general"]},
    {"latin": "Bromus tectorum", "hu": "tetőrozsnok", "group": "grass", "crop_tags": ["wheat", "general"]},
    {"latin": "Bromus secalinus", "hu": "rozs-rozsnok", "group": "grass", "crop_tags": ["wheat", "general"]},
    {"latin": "Lolium rigidum", "hu": "merev perje", "group": "grass", "crop_tags": ["wheat", "general"]},
    {"latin": "Lolium multiflorum", "hu": "olaszperje", "group": "grass", "crop_tags": ["general"]},
    {"latin": "Lolium perenne", "hu": "angolperje", "group": "grass", "crop_tags": ["general"]},
    {"latin": "Poa annua", "hu": "egynyári perje", "group": "grass", "crop_tags": ["wheat", "general"]},
    {"latin": "Poa trivialis", "hu": "réti perje", "group": "grass", "crop_tags": ["wheat", "general"]},
    {"latin": "Vulpia myuros", "hu": "egérfarkú perje", "group": "grass", "crop_tags": ["wheat", "general"]},
    {"latin": "Vulpia bromoides", "hu": "rozsnok-perje", "group": "grass", "crop_tags": ["wheat", "general"]},
    {"latin": "Elymus repens", "hu": "tarackbúza", "group": "grass", "crop_tags": ["general"]},

    # EGYSZIKŰ / FŰFÉLÉK (kapásokban kiemelten)
    {"latin": "Echinochloa crus-galli", "hu": "kakaslábfű", "group": "grass", "crop_tags": ["maize", "sunflower", "soy", "beet", "general"]},
    {"latin": "Setaria viridis", "hu": "zöld muhar", "group": "grass", "crop_tags": ["maize", "sunflower", "soy", "beet", "general"]},
    {"latin": "Setaria pumila", "hu": "sárga muhar", "group": "grass", "crop_tags": ["maize", "sunflower", "soy", "beet", "general"]},
    {"latin": "Digitaria sanguinalis", "hu": "vérpiros muhar", "group": "grass", "crop_tags": ["maize", "sunflower", "soy", "general"]},
    {"latin": "Sorghum halepense", "hu": "fenyércirok", "group": "grass", "crop_tags": ["maize", "sunflower", "soy", "beet", "general"]},

    # SÁS / ZSURLÓ
    {"latin": "Cyperus esculentus", "hu": "földi mandula (zsombor)", "group": "sedge", "crop_tags": ["maize", "sunflower", "general"]},
    {"latin": "Equisetum arvense", "hu": "mezei zsurló", "group": "horsetail", "crop_tags": ["general"]},

    # KÉTSZIKŰEK – gyakori őszi/koratavaszi (kalászos/repce)
    {"latin": "Capsella bursa-pastoris", "hu": "pásztortáska", "group": "broadleaf", "crop_tags": ["wheat", "rape", "general"]},
    {"latin": "Thlaspi arvense", "hu": "mezei tarsóka", "group": "broadleaf", "crop_tags": ["wheat", "rape", "general"]},
    {"latin": "Descurainia sophia", "hu": "büdös zsombor", "group": "broadleaf", "crop_tags": ["wheat", "rape", "general"]},
    {"latin": "Sinapis arvensis", "hu": "vadrepce", "group": "broadleaf", "crop_tags": ["wheat", "rape", "maize", "sunflower", "general"]},
    {"latin": "Raphanus raphanistrum", "hu": "vadretek", "group": "broadleaf", "crop_tags": ["wheat", "rape", "maize", "sunflower", "general"]},
    {"latin": "Tripleurospermum inodorum", "hu": "ebszékfű", "group": "broadleaf", "crop_tags": ["wheat", "rape", "general"]},
    {"latin": "Papaver rhoeas", "hu": "pipacs", "group": "broadleaf", "crop_tags": ["wheat", "rape", "general"]},
    {"latin": "Fumaria officinalis", "hu": "orvosi füstike", "group": "broadleaf", "crop_tags": ["wheat", "rape", "general"]},
    {"latin": "Viola arvensis", "hu": "mezei árvácska", "group": "broadleaf", "crop_tags": ["wheat", "rape", "general"]},
    {"latin": "Stellaria media", "hu": "tyúkhúr", "group": "broadleaf", "crop_tags": ["wheat", "rape", "general"]},
    {"latin": "Veronica persica", "hu": "perzsa veronika", "group": "broadleaf", "crop_tags": ["wheat", "rape", "general"]},
    {"latin": "Veronica hederifolia", "hu": "borostyánlevelű veronika", "group": "broadleaf", "crop_tags": ["wheat", "rape", "general"]},
    {"latin": "Lamium purpureum", "hu": "piros árvacsalán", "group": "broadleaf", "crop_tags": ["wheat", "rape", "general"]},
    {"latin": "Lamium amplexicaule", "hu": "szárölelő árvacsalán", "group": "broadleaf", "crop_tags": ["wheat", "rape", "general"]},
    {"latin": "Galium aparine", "hu": "ragadós galaj", "group": "broadleaf", "crop_tags": ["wheat", "rape", "general"]},
    {"latin": "Geranium dissectum", "hu": "vágottlevelű gólyaorr", "group": "broadleaf", "crop_tags": ["wheat", "rape", "general"]},

    # KÉTSZIKŰEK – kapások (tavaszi/nyári)
    {"latin": "Chenopodium album", "hu": "fehér libatop", "group": "broadleaf", "crop_tags": ["maize", "sunflower", "soy", "beet", "general"]},
    {"latin": "Amaranthus retroflexus", "hu": "szőrös disznóparéj", "group": "broadleaf", "crop_tags": ["maize", "sunflower", "soy", "beet", "general"]},
    {"latin": "Ambrosia artemisiifolia", "hu": "parlagfű", "group": "broadleaf", "crop_tags": ["maize", "sunflower", "soy", "general"]},
    {"latin": "Datura stramonium", "hu": "csattanó maszlag", "group": "broadleaf", "crop_tags": ["maize", "sunflower", "soy", "general"]},
    {"latin": "Abutilon theophrasti", "hu": "selyemmályva", "group": "broadleaf", "crop_tags": ["maize", "sunflower", "soy", "general"]},
    {"latin": "Solanum nigrum", "hu": "fekete csucsor", "group": "broadleaf", "crop_tags": ["maize", "sunflower", "soy", "general"]},
    {"latin": "Convolvulus arvensis", "hu": "mezei szulák", "group": "broadleaf", "crop_tags": ["general"]},
    {"latin": "Cirsium arvense", "hu": "mezei acat", "group": "broadleaf", "crop_tags": ["general"]},

    # KESERŰFŰFÉLÉK / Polygonaceae
    {"latin": "Polygonum aviculare", "hu": "madárkeserűfű", "group": "broadleaf", "crop_tags": ["maize", "sunflower", "soy", "beet", "general"]},
    {"latin": "Persicaria maculosa", "hu": "foltos keserűfű", "group": "broadleaf", "crop_tags": ["maize", "sunflower", "soy", "beet", "general"]},
    {"latin": "Persicaria lapathifolia", "hu": "baracklevelű keserűfű", "group": "broadleaf", "crop_tags": ["maize", "sunflower", "soy", "beet", "general"]},
    {"latin": "Fallopia convolvulus", "hu": "sövénykeserűfű", "group": "broadleaf", "crop_tags": ["maize", "sunflower", "soy", "general"]},
]

FIELD_WEEDS_INDEX: Dict[str, Dict[str, Any]] = {w["latin"].lower(): w for w in FIELD_WEEDS_HU}


# -------------------------
# Helpers
# -------------------------
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

    url = f"{base_url}/v2/identify/{project}?api-key={api_key}"

    files = {"images": ("image.jpg", image_bytes, "image/jpeg")}

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


def simplify_plantnet_response(raw: Dict[str, Any], top_k: int = 10) -> Dict[str, Any]:
    results = raw.get("results", []) or []
    simple: List[Dict[str, Any]] = []

    for item in results[:top_k]:
        species = item.get("species", {}) or {}
        sci = species.get("scientificNameWithoutAuthor") or species.get("scientificName") or "unknown"
        simple.append(
            {
                "scientific_name": sci,
                "confidence": item.get("score", None),
                "common_names": (species.get("commonNames") or [])[:5],
            }
        )

    return {
        "engine": "plantnet",
        "project": raw.get("project") or os.getenv("PLANTNET_PROJECT", "k-middle-europe"),
        "top_k": top_k,
        "candidates": simple,
    }


def filter_to_field_weeds(candidates: List[Dict[str, Any]], crop: Optional[str] = None) -> Dict[str, Any]:
    crop_norm = (crop or "").strip().lower() or None

    kept: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []

    for c in candidates:
        latin = (c.get("scientific_name") or "").strip()
        rec = FIELD_WEEDS_INDEX.get(latin.lower())

        if not rec:
            dropped.append({**c, "reason": "not_in_field_weed_db"})
            continue

        if crop_norm and (crop_norm not in rec["crop_tags"]) and ("general" not in rec["crop_tags"]):
            dropped.append({**c, "reason": f"not_typical_for_crop:{crop_norm}", "hu": rec["hu"]})
            continue

        kept.append({**c, "hu_name": rec["hu"], "group": rec["group"], "crop_tags": rec["crop_tags"]})

    top = kept[0] if kept else (candidates[0] if candidates else None)
    conf = (top or {}).get("confidence")

    if isinstance(conf, (int, float)):
        level = "high" if conf >= 0.75 else "medium" if conf >= 0.4 else "low"
    else:
        level = "unknown"

    return {"top": top, "confidence_level": level, "kept": kept, "dropped": dropped}


# -------------------------
# Routes
# -------------------------
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


import base64
from fastapi import Request

@app.post("/v1/diagnosztika_json", tags=["diagnosis"])
async def diagnose_json(request: Request):
    data = await request.json()
    mode = data.get("mode", "weed")
    crop = data.get("crop")
    note = data.get("note")
    debug = bool(data.get("debug", False))
    image_b64 = data.get("image_b64")

    if not image_b64 or not isinstance(image_b64, str):
        raise HTTPException(status_code=400, detail="Hiányzik: image_b64")

    # Elfogadjuk, ha data URL (data:image/jpeg;base64,....)
    if "base64," in image_b64:
        image_b64 = image_b64.split("base64,", 1)[1]

    try:
        image_bytes = base64.b64decode(image_b64, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="Érvénytelen base64 (image_b64).")

    raw = call_plantnet(image_bytes)
    plantnet = simplify_plantnet_response(raw, top_k=10)
    weed_pack = filter_to_field_weeds(plantnet["candidates"], crop=crop)
    top = weed_pack["top"] or {}

    gpt_friendly = {
        "top_species": top.get("scientific_name"),
        "top_hu_name": top.get("hu_name"),
        "confidence": top.get("confidence"),
        "confidence_level": weed_pack["confidence_level"],
        "filtered_candidates": weed_pack["kept"][:5],
        "note": "PlantNet jelöltek szántóföldi gyom adatbázissal szűrve (crop-aware).",
    }

    if debug:
        gpt_friendly["dropped_preview"] = weed_pack["dropped"][:10]
        gpt_friendly["image_debug"] = _image_debug_info(image_bytes)

    return JSONResponse(
        {
            "ok": True,
            "request": {"mode": mode, "crop": crop, "note": note, "input": "json_base64"},
            "plantnet": plantnet,
            "gpt_friendly": gpt_friendly,
        }
    ):
    ...
    if mode not in ("weed", "disease", "pest", "crop", "auto"):
        raise HTTPException(status_code=400, detail="Érvénytelen mode.")

    image_bytes = await image.read()
    if not image_bytes or len(image_bytes) < 50:
        raise HTTPException(status_code=400, detail="Üres / túl kicsi kép.")

    raw = call_plantnet(image_bytes)
    plantnet = simplify_plantnet_response(raw, top_k=10)

    weed_pack = filter_to_field_weeds(plantnet["candidates"], crop=crop)
    top = weed_pack["top"] or {}

    gpt_friendly = {
        "top_species": top.get("scientific_name"),
        "top_hu_name": top.get("hu_name"),
        "confidence": top.get("confidence"),
        "confidence_level": weed_pack["confidence_level"],
        "filtered_candidates": weed_pack["kept"][:5],
        "note": "PlantNet jelöltek szántóföldi gyom adatbázissal szűrve (crop-aware).",
    }

    if debug:
        gpt_friendly["dropped_preview"] = weed_pack["dropped"][:10]
        gpt_friendly["image_debug"] = _image_debug_info(image_bytes)

    payload: Dict[str, Any] = {
        "ok": True,
        "request": {
            "mode": mode,
            "crop": crop,
            "filename": image.filename,
            "content_type": image.content_type,
            "note": note,
        },
        "plantnet": plantnet,
        "gpt_friendly": gpt_friendly,
    }

    return JSONResponse(payload)
