import os
import base64
import re
from typing import Literal, Optional, Any, Dict

import httpx
from fastapi import FastAPI, File, UploadFile, Query, Body, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


# -----------------------------
# Config
# -----------------------------
PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()

# PlantNet v2 endpoint (ez a tipikus helyes forma)
PLANTNET_BASE = "https://my-api.plantnet.org/v2/identify"


# -----------------------------
# Models
# -----------------------------
Organ = Literal["leaf", "flower", "fruit", "bark"]

class Confidence(BaseModel):
    top1_score: float = Field(..., ge=0.0, le=1.0)
    level: str

class Match(BaseModel):
    name: str
    score: float = Field(..., ge=0.0, le=1.0)

class IdentifyResponse(BaseModel):
    bestMatch: str
    confidence: Confidence
    topMatches: list[Match]

class IdentifyB64Request(BaseModel):
    image_base64: str = Field(..., description="Base64 string vagy data URL (pl. data:image/jpeg;base64,...)")
    organs: Organ = "leaf"

class ErrorResponse(BaseModel):
    detail: Any


# -----------------------------
# App
# -----------------------------
app = FastAPI(title="Növénydiagnosztikai API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


# -----------------------------
# Helpers
# -----------------------------
_DATA_URL_RE = re.compile(r"^data:(image\/[a-zA-Z0-9.+-]+);base64,(.*)$", re.DOTALL)

def decode_image_base64(s: str) -> bytes:
    s = s.strip()

    m = _DATA_URL_RE.match(s)
    if m:
        s = m.group(2).strip()

    # whitespace törlés
    s = re.sub(r"\s+", "", s)

    try:
        return base64.b64decode(s, validate=True)
    except Exception:
        # ha padding gond van, próbáljuk javítani
        pad = (-len(s)) % 4
        if pad:
            s2 = s + ("=" * pad)
            try:
                return base64.b64decode(s2, validate=True)
            except Exception:
                pass
        raise HTTPException(status_code=422, detail="Nem érvényes base64: Incorrect padding")


async def call_plantnet(image_bytes: bytes, organs: str) -> Dict[str, Any]:
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="Hiányzik a PLANTNET_API_KEY (Render Environment-ben add hozzá).")

    url = f"{PLANTNET_BASE}/all"
    params = {"api-key": PLANTNET_API_KEY}

    # PlantNet: multipart - "images" mező néven is előfordul dokumentációtól függően.
    # A te Swagger curl mintád az "image" mezőt használta a saját API-dnál,
    # PlantNet felé viszont a biztonság kedvéért "images" néven küldjük.
    files = {
        "images": ("image.jpg", image_bytes, "image/jpeg"),
    }
    data = {"organs": organs}

    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(url, params=params, data=data, files=files)

    # ha PlantNet 404/401/400, add vissza részletesen
    if r.status_code != 200:
        try:
            payload = r.json()
        except Exception:
            payload = {"text": r.text}

        raise HTTPException(
            status_code=502,
            detail={
                "plantnet_status": r.status_code,
                "plantnet_error": payload,
            },
        )

    return r.json()


def normalize_plantnet_result(payload: Dict[str, Any]) -> IdentifyResponse:
    """
    PlantNet válasz formátuma változhat. Itt egy robusztus normalizálás:
    - bestMatch: legjobb találat neve
    - topMatches: első 5 találat (name, score)
    - confidence.top1_score: top1 score (ha nincs, 0)
    - confidence.level: 'species' (ha nem tudjuk)
    """
    results = payload.get("results") or []

    top_matches: list[Match] = []
    for item in results[:5]:
        sp = item.get("species") or {}
        name = sp.get("scientificNameWithoutAuthor") or sp.get("scientificName") or sp.get("commonNames", [""])[0] or "Unknown"
        score = float(item.get("score") or 0.0)
        top_matches.append(Match(name=name, score=score))

    if top_matches:
        best = top_matches[0].name
        top1 = top_matches[0].score
    else:
        best = "Unknown"
        top1 = 0.0

    return IdentifyResponse(
        bestMatch=best,
        confidence=Confidence(top1_score=top1, level="species"),
        topMatches=top_matches,
    )


# -----------------------------
# Endpoints
# -----------------------------
@app.post("/identify", response_model=IdentifyResponse, responses={422: {"model": ErrorResponse}})
async def identifyPlant(
    image: UploadFile = File(..., description="Kép (jpg/png/webp)"),
    organs: Organ = Query("leaf", description="leaf/flower/fruit/bark"),
):
    image_bytes = await image.read()
    payload = await call_plantnet(image_bytes=image_bytes, organs=organs)
    return normalize_plantnet_result(payload)


@app.post("/identify_b64", response_model=IdentifyResponse, responses={422: {"model": ErrorResponse}})
async def identifyPlantB64(body: IdentifyB64Request = Body(...)):
    image_bytes = decode_image_base64(body.image_base64)
    payload = await call_plantnet(image_bytes=image_bytes, organs=body.organs)
    return normalize_plantnet_result(payload)
            
