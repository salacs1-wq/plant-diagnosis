import os
import base64
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, File, UploadFile, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


# ----------------------------
# Config
# ----------------------------
PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_PROJECT = os.getenv("PLANTNET_PROJECT", "weurope").strip()  # pl. weurope / all
PLANTNET_BASE_URL = os.getenv("PLANTNET_BASE_URL", "https://my-api.plantnet.org").rstrip("/")

if not PLANTNET_API_KEY:
    # Nem dobunk itt hibát importkor (Render deploy), csak a híváskor fogjuk jelezni.
    pass


# ----------------------------
# FastAPI app
# ----------------------------
app = FastAPI(
    title="Növénydiagnosztikai API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------------
# Models
# ----------------------------
class IdentifyB64Request(BaseModel):
    image_base64: str  # base64 vagy data URL
    organs: Optional[str] = "leaf"


# ----------------------------
# Helpers
# ----------------------------
def _ensure_api_key() -> str:
    if not PLANTNET_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Hiányzó PLANTNET_API_KEY környezeti változó (Render Environment-ben add meg).",
        )
    return PLANTNET_API_KEY


def _normalize_organs(organs: Optional[str]) -> str:
    if not organs:
        return "leaf"
    # engedünk: "leaf", "flower", stb. + esetleg több, vesszővel
    return organs.strip()


def _extract_b64_payload(image_base64: str) -> bytes:
    """
    Elfogad:
      - tiszta base64 stringet
      - data URL-t: data:image/jpeg;base64,AAAA...
    """
    s = (image_base64 or "").strip()
    if not s:
        raise HTTPException(status_code=422, detail="image_base64 üres")

    if s.startswith("data:"):
        # data:image/jpeg;base64,XXXXX
        try:
            s = s.split(",", 1)[1]
        except Exception:
            raise HTTPException(status_code=422, detail="Hibás data URL formátum")

    # Padding javítás (ha hiányzik)
    missing_padding = (-len(s)) % 4
    if missing_padding:
        s += "=" * missing_padding

    try:
        return base64.b64decode(s, validate=False)
    except Exception:
        raise HTTPException(status_code=422, detail="Nem érvényes base64 (decode hiba)")


async def _call_plantnet_identify(image_bytes: bytes, filename: str, organs: str) -> Dict[str, Any]:
    """
    PlantNet: POST /v2/identify/{project}?api-key=...
    Form-data: images[] + organs (tömb vagy string)
    """
    api_key = _ensure_api_key()
    project = PLANTNET_PROJECT or "weurope"

    url = f"{PLANTNET_BASE_URL}/v2/identify/{project}"
    params = {"api-key": api_key}

    # PlantNet több képet is fogad (images[]), mi most 1 képpel hívjuk
    files = {
        "images": (filename or "image.jpg", image_bytes, "application/octet-stream"),
    }

    # organs param PlantNetnél lehet lista jellegű; egyszerűen küldjük stringként
    data = {
        "organs": organs,  # pl. leaf/flower/fruit/bark
        # opcionálisak:
        # "include-related-images": "false",
        # "no-reject": "false",
        # "lang": "hu",
    }

    timeout = httpx.Timeout(60.0, connect=30.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, params=params, data=data, files=files)

    # 200 OK esetén JSON
    if r.status_code == 200:
        return r.json()

    # Hibák visszaadása olvashatóan
    try:
        err = r.json()
    except Exception:
        err = {"text": r.text}

    raise HTTPException(
        status_code=502,
        detail={
            "plantnet_status": r.status_code,
            "plantnet_error": err,
        },
    )


def _to_simple_response(plantnet_json: Dict[str, Any]) -> Dict[str, Any]:
    """
    A PlantNet válaszából készítünk egy egyszerű, GPT-nek barát formát:
      - bestMatch
      - confidence.top1_score + level
      - topMatches: [{name, score}, ...]
    """
    results = plantnet_json.get("results") or []
    top_matches: List[Dict[str, Any]] = []

    for item in results[:5]:
        species = item.get("species") or {}
        sci = species.get("scientificNameWithoutAuthor") or species.get("scientificName") or "Unknown"
        score = item.get("score")
        top_matches.append({"name": sci, "score": float(score) if score is not None else 0.0})

    best = top_matches[0]["name"] if top_matches else "Unknown"
    top1 = top_matches[0]["score"] if top_matches else 0.0

    return {
        "bestMatch": best,
        "confidence": {"top1_score": top1, "level": "species"},
        "topMatches": top_matches,
        "raw": plantnet_json,  # ha nem kell, kiveheted
    }


# ----------------------------
# Routes
# ----------------------------
@app.get("/health")
async def health_get() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/identify")
async def identify_plant(
    image: UploadFile = File(...),
    organs: str = Query(default="leaf", description="Például leaf/flower/fruit/bark"),
) -> Dict[str, Any]:
    organs = _normalize_organs(organs)

    # fájl beolvasása
    try:
        content = await image.read()
    except Exception:
        raise HTTPException(status_code=422, detail="Nem tudom beolvasni a feltöltött képet")

    plantnet_json = await _call_plantnet_identify(content, image.filename or "image.jpg", organs)
    return _to_simple_response(plantnet_json)


@app.post("/identify_b64")
async def identify_plant_b64(payload: IdentifyB64Request) -> Dict[str, Any]:
    organs = _normalize_organs(payload.organs)

    img_bytes = _extract_b64_payload(payload.image_base64)
    plantnet_json = await _call_plantnet_identify(img_bytes, "image.jpg", organs)
    return _to_simple_response(plantnet_json)
