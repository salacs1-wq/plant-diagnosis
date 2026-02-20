import os
import base64
import re
from typing import Optional, Tuple, Any, Dict, List

import requests
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ===== Config =====
PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()

# FONTOS JAVÍTÁS: NINCS /all A VÉGÉN
PLANTNET_BASE_URL = os.getenv("PLANTNET_BASE_URL", "https://my-api.plantnet.org").strip()
PLANTNET_IDENTIFY_URL = f"{PLANTNET_BASE_URL}/v2/identify"

ALLOWED_CT = {"image/jpeg", "image/png", "image/webp"}

app = FastAPI(title="Növénydiagnosztikai API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== Models =====
class IdentifyB64Request(BaseModel):
    image_base64: str = Field(..., description="Raw base64, data URL, vagy /mnt/data/... fájlútvonal")
    organs: str = Field("leaf", description="leaf/flower/fruit/bark/auto")

# ===== Segédfüggvények =====

def _strip_data_url(s: str) -> Tuple[Optional[str], str]:
    m = re.match(r"^data:(image\/[a-zA-Z0-9\-\+\.]+);base64,(.+)$", s.strip())
    if not m:
        return None, s.strip()
    return m.group(1).lower(), m.group(2)


def _maybe_read_mnt_path(s: str) -> Optional[bytes]:
    s = s.strip()
    if s.startswith("/mnt/") and os.path.exists(s) and os.path.isfile(s):
        with open(s, "rb") as f:
            return f.read()
    return None


def _decode_image_from_b64_or_path(image_base64_or_path: str) -> Tuple[bytes, str]:
    # 1) fájlútvonal?
    b = _maybe_read_mnt_path(image_base64_or_path)
    if b is not None:
        return b, "image/jpeg"

    # 2) data URL?
    mime, payload = _strip_data_url(image_base64_or_path)
    if mime is None:
        mime = "image/jpeg"

    # 3) base64 dekód
    try:
        raw = base64.b64decode(payload, validate=True)
    except Exception:
        try:
            raw = base64.b64decode(payload)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Nem érvényes base64: {e}")

    return raw, mime


def _call_plantnet(image_bytes: bytes, filename: str, content_type: str, organs: str) -> Dict[str, Any]:
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=502, detail="PLANTNET_API_KEY nincs beállítva.")

    params = {"api-key": PLANTNET_API_KEY}

    files = [
        ("images", (filename, image_bytes, content_type)),
    ]

    data = [
        ("organs", organs or "auto"),
    ]

    try:
        r = requests.post(
            PLANTNET_IDENTIFY_URL,
            params=params,
            files=files,
            data=data,
            timeout=60,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"PlantNet hívás sikertelen: {e}")

    if r.status_code >= 400:
        try:
            j = r.json()
        except Exception:
            j = {"text": r.text[:1000]}
        raise HTTPException(
            status_code=502,
            detail={
                "plantnet_status": r.status_code,
                "plantnet_error": j
            },
        )

    try:
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"PlantNet válasz nem JSON: {e}")


def _simplify_plantnet_response(j: Dict[str, Any]) -> Dict[str, Any]:
    results = j.get("results") or []
    top_matches: List[Dict[str, Any]] = []

    def _species_name(res: Dict[str, Any]) -> str:
        sp = res.get("species") or {}
        return (
            sp.get("scientificNameWithoutAuthor")
            or sp.get("scientificName")
            or (sp.get("commonNames") or [None])[0]
            or "Unknown"
        )

    for res in results[:5]:
        name = _species_name(res)
        score = float(res.get("score", 0.0))
        top_matches.append({"name": name, "score": score})

    best = top_matches[0]["name"] if top_matches else "Unknown"
    top1 = top_matches[0]["score"] if top_matches else 0.0

    return {
        "bestMatch": best,
        "confidence": {
            "top1_score": top1,
            "level": "species"
        },
        "topMatches": top_matches,
    }

# ===== Endpoints =====

@app.get("/health")
def health_get():
    return {"status": "ok"}


@app.post("/identify")
async def identify_plant(
    image: UploadFile = File(...),
    organs: str = Form("leaf"),
):
    if image.content_type not in ALLOWED_CT:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type: {image.content_type}",
        )

    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=422, detail="Üres fájl.")

    plantnet_json = _call_plantnet(
        image_bytes=image_bytes,
        filename=image.filename or "image.jpg",
        content_type=image.content_type or "image/jpeg",
        organs=organs,
    )

    return _simplify_plantnet_response(plantnet_json)


@app.post("/identify_b64")
def identify_plant_b64(req: IdentifyB64Request):
    img_bytes, mime = _decode_image_from_b64_or_path(req.image_base64)

    plantnet_json = _call_plantnet(
        image_bytes=img_bytes,
        filename="image.jpg",
        content_type=(mime if mime in ALLOWED_CT else "image/jpeg"),
        organs=req.organs,
    )

    return _simplify_plantnet_response(plantnet_json)
