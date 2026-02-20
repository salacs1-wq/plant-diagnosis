# main.py
# Követelmények (requirements.txt): fastapi, uvicorn, httpx, python-multipart
# Render indítás (Start Command): uvicorn main:app --host 0.0.0.0 --port $PORT

from __future__ import annotations

import base64
import binascii
import os
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ----------------------------
# Konfiguráció (Render ENV)
# ----------------------------
PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_PROJECT = os.getenv("PLANTNET_PROJECT", "weurope").strip() # pl. weurope, europe, france, etc.
PLANTNET_BASE = "https://my-api.plantnet.org/v2/identify"

# ----------------------------
# Pydantic modellek
# ----------------------------

class Confidence(BaseModel):
    top1_score: float = Field(..., ge=0.0, le=1.0)
    level: str = Field(..., description="Confidence level label (e.g., species, genus, family)")


class MatchItem(BaseModel):
    name: str
    score: float = Field(..., ge=0.0, le=1.0)


class IdentifyResponse(BaseModel):
    bestMatch: str
    confidence: Confidence
    topMatches: List[MatchItem]


class ErrorResponse(BaseModel):
    detail: Any


class IdentifyB64Request(BaseModel):
    image_base64: str = Field(..., description="Base64 string vagy data URL (pl. data:image/jpeg;base64,...)")
    organs: str = Field("leaf", description="Például leaf/flower/fruit/bark")


# ----------------------------
# App
# ----------------------------
app = FastAPI(
    title="Növénydiagnosztikai API",
    version="1.0.0",
)

# CORS (ha UI-ból / ChatGPT toolból hívod)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------
# Segédfüggvények
# ----------------------------

def _strip_data_url(b64_or_data_url: str) -> Tuple[str, Optional[str]]:
    """
    Visszaadja: (pure_base64, mime_type_or_None)
    Kezeli a data URL formát: data:image/jpeg;base64,AAAA...
    """
    s = (b64_or_data_url or "").strip()
    if s.lower().startswith("data:") and ";base64," in s.lower():
        header, b64 = s.split(",", 1)
        # header pl: data:image/jpeg;base64
        mime = header.split(";", 1)[0].split(":", 1)[-1].strip() or None
        return b64.strip(), mime
    return s, None


def _safe_b64decode(b64_or_data_url: str) -> Tuple[bytes, str]:
    """
    Base64 dekódolás padding-javítással.
    Visszaadja: (bytes, mime_guess)
    """
    b64, mime = _strip_data_url(b64_or_data_url)

    # whitespace törlés
    b64 = "".join(b64.split())

    # padding javítás
    missing = (-len(b64)) % 4
    if missing:
        b64 += "=" * missing

    try:
        data = base64.b64decode(b64, validate=False)
    except (binascii.Error, ValueError) as e:
        raise HTTPException(status_code=422, detail=f"Nem érvényes base64: {e}")

    # MIME tipp
    if mime:
        return data, mime
    # Magic bytes alapú minimál tipp
    if data.startswith(b"\xFF\xD8\xFF"):
        return data, "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return data, "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return data, "image/webp"
    return data, "application/octet-stream"


def _extract_bestmatch_and_score(plantnet_json: Dict[str, Any]) -> Tuple[str, float, str, List[Tuple[str, float]]]:
    """
    PlantNet válasz normalizálása a saját sémánkra:
    - bestMatch: "Capsella bursa-pastoris"
    - top1_score: 0.1359
    - level: "species" (ha van), különben "species" default
    - topMatches: [(name, score), ...]
    """
    results = plantnet_json.get("results") or []
    if not isinstance(results, list) or len(results) == 0:
        raise HTTPException(status_code=502, detail={"plantnet_error": "Üres vagy hibás PlantNet válasz", "raw": plantnet_json})

    top_matches: List[Tuple[str, float]] = []
    for r in results[:10]:
        if not isinstance(r, dict):
            continue
        score = float(r.get("score") or 0.0)
        species = r.get("species") or {}
        sci = ""
        if isinstance(species, dict):
            sci = (species.get("scientificNameWithoutAuthor") or species.get("scientificName") or "").strip()
        if sci:
            top_matches.append((sci, score))

    if not top_matches:
        # fallback: próbáljuk meg más mezőkből
        r0 = results[0]
        score0 = float(r0.get("score") or 0.0) if isinstance(r0, dict) else 0.0
        top_matches = [("Unknown", score0)]

    best_name, best_score = top_matches[0]

    # "level" (PlantNet nem mindig ad explicit level mezőt)
    level = "species"
    # néha: plantnet_json["bestMatch"] nincs, ezért mi képezzük
    return best_name, best_score, level, top_matches


async def _call_plantnet(image_bytes: bytes, mime_type: str, organs: str) -> Dict[str, Any]:
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="Hiányzik a PLANTNET_API_KEY környezeti változó (Render Environment).")

    if not PLANTNET_PROJECT:
        raise HTTPException(status_code=500, detail="Hiányzik a PLANTNET_PROJECT környezeti változó (pl. weurope).")

    url = f"{PLANTNET_BASE}/{PLANTNET_PROJECT}"
    params = {"api-key": PLANTNET_API_KEY}

    # PlantNet v2 mezők:
    # - files: images
    # - data: organs (többször is lehet; itt 1 db)
    files = {"images": ("image", image_bytes, mime_type or "image/jpeg")}
    data = {"organs": organs or "leaf"}

    timeout = httpx.Timeout(60.0, connect=20.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, params=params, data=data, files=files)

    # PlantNet hibát visszaadjuk 502-be csomagolva (hogy a tool oldalon látszódjon)
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


def _normalize_response(plantnet_json: Dict[str, Any]) -> IdentifyResponse:
    best, score, level, top = _extract_bestmatch_and_score(plantnet_json)
    top_matches = [MatchItem(name=n, score=float(s)) for (n, s) in top[:10]]
    return IdentifyResponse(
        bestMatch=best,
        confidence=Confidence(top1_score=float(score), level=level),
        topMatches=top_matches,
    )


# ----------------------------
# Endpontok
# ----------------------------

@app.get("/health", operation_id="healthGet", summary="Egészség ellenőrzés")
async def health():
    return {"status": "ok"}


@app.post(
    "/identify",
    operation_id="identifyPlant",
    summary="Növény azonosítása képfájlból (multipart)",
    response_model=IdentifyResponse,
    responses={422: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
)
async def identify(
    image: UploadFile = File(..., description="Kép (jpg/png/webp)"),
    organs: str = Query("leaf", description="Például leaf/flower/fruit/bark"),
):
    # MIME ellenőrzés
    ct = (image.content_type or "").lower().strip()
    if ct not in {"image/jpeg", "image/jpg", "image/png", "image/webp"}:
        # sok kliens 'image/jpg'-ot küld; normalizáljuk
        if ct == "image/jpg":
            ct = "image/jpeg"
        else:
            raise HTTPException(status_code=422, detail=f"Unsupported file type: {image.content_type}")

    img_bytes = await image.read()
    if not img_bytes:
        raise HTTPException(status_code=422, detail="Üres kép fájl.")

    plantnet_json = await _call_plantnet(img_bytes, ct, organs)
    return _normalize_response(plantnet_json)


@app.post(
    "/identify_b64",
    operation_id="identifyPlantB64",
    summary="Növény azonosítása base64 képből (JSON body)",
    response_model=IdentifyResponse,
    responses={422: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
)
async def identify_b64(payload: IdentifyB64Request):
    img_bytes, mime = _safe_b64decode(payload.image_base64)
    if not img_bytes:
        raise HTTPException(status_code=422, detail="Üres base64 kép.")

    # ha nem képfájl, próbáljuk mégis jpeg-ként (de jobb, ha a kliens jól küldi)
    if mime not in {"image/jpeg", "image/png", "image/webp"}:
        # lehet octet-stream; ilyenkor marad
        pass

    plantnet_json = await _call_plantnet(img_bytes, mime, payload.organs)
    return _normalize_response(plantnet_json)


# Opcionális: root, hogy ne csak 404 legyen a domain gyökerén
@app.get("/", include_in_schema=False)
async def root():
    return {"status": "ok", "docs": "/docs", "openapi": "/openapi.json"}
