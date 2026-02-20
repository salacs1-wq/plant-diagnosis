# main.py
import os
import base64
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


# ----------------------------
# Config (Render: Environment)
# ----------------------------
PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()

# PlantNet v2 default (override if needed)
PLANTNET_BASE_URL = os.getenv("PLANTNET_BASE_URL", "https://my-api.plantnet.org").rstrip("/")
PLANTNET_ENDPOINT = os.getenv("PLANTNET_ENDPOINT", "/v2/identify/all").strip()  # keep leading slash

# Optional: improve results / debugging
PLANTNET_LANG = os.getenv("PLANTNET_LANG", "en")
PLANTNET_INCLUDE_RELATED = os.getenv("PLANTNET_INCLUDE_RELATED_IMAGES", "false").lower() in ("1", "true", "yes")


ALLOWED_CT = {"image/jpeg", "image/png", "image/webp"}
DEFAULT_ORGANS = "leaf"


# ----------------------------
# API models
# ----------------------------
class Confidence(BaseModel):
    top1_score: float
    level: str


class Match(BaseModel):
    name: str
    score: float


class IdentifyResponse(BaseModel):
    bestMatch: str
    confidence: Confidence
    topMatches: List[Match]


class IdentifyB64Request(BaseModel):
    image_base64: str
    organs: Optional[str] = DEFAULT_ORGANS


# ----------------------------
# FastAPI app
# ----------------------------
app = FastAPI(
    title="Növénydiagnosztikai API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # egyszerű terepi használatra
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------------
# Helpers
# ----------------------------
def _clean_data_url(b64_or_dataurl: str) -> str:
    """
    Accepts either:
      - raw base64: "AAAA..."
      - data URL: "data:image/jpeg;base64,AAAA..."
    Returns raw base64 part.
    """
    s = (b64_or_dataurl or "").strip()
    if s.startswith("data:"):
        # data:image/jpeg;base64,....
        parts = s.split(",", 1)
        if len(parts) != 2:
            raise HTTPException(status_code=422, detail="Nem érvényes data URL formátum.")
        return parts[1].strip()
    return s


def _guess_content_type_from_header(dataurl: str) -> Optional[str]:
    if not dataurl or not dataurl.startswith("data:"):
        return None
    header = dataurl.split(",", 1)[0].lower()
    # data:image/jpeg;base64
    if "image/jpeg" in header or "image/jpg" in header:
        return "image/jpeg"
    if "image/png" in header:
        return "image/png"
    if "image/webp" in header:
        return "image/webp"
    return None


async def _call_plantnet(image_bytes: bytes, filename: str, content_type: str, organs: str) -> Dict[str, Any]:
    """
    Calls PlantNet API and returns parsed JSON.
    Uses env vars for endpoint and api-key.
    """
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=502, detail="PlantNet API kulcs hiányzik (PLANTNET_API_KEY).")

    url = f"{PLANTNET_BASE_URL}{PLANTNET_ENDPOINT}"
    params = {
        "api-key": PLANTNET_API_KEY,
        "lang": PLANTNET_LANG,
    }
    if PLANTNET_INCLUDE_RELATED:
        params["include-related-images"] = "true"

    # PlantNet typically expects:
    #  - files: images
    #  - data: organs (and optional others)
    data = {"organs": organs or DEFAULT_ORGANS}

    files = {
        "images": (filename or "image.jpg", image_bytes, content_type),
    }

    timeout = httpx.Timeout(60.0, connect=15.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, params=params, data=data, files=files)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"PlantNet elérés sikertelen: {type(e).__name__}")

    # PlantNet errors are usually JSON; keep some details
    if resp.status_code >= 400:
        detail_text = None
        try:
            detail_text = resp.json()
        except Exception:
            detail_text = resp.text[:500] if resp.text else "Ismeretlen hiba"
        raise HTTPException(status_code=502, detail={"plantnet_status": resp.status_code, "plantnet_error": detail_text})

    try:
        return resp.json()
    except Exception:
        raise HTTPException(status_code=502, detail="PlantNet válasz nem JSON.")


def _to_identify_response(plantnet_json: Dict[str, Any]) -> IdentifyResponse:
    """
    Normalizes PlantNet output to:
      { bestMatch, confidence:{top1_score, level}, topMatches:[{name,score}...] }
    """
    results = plantnet_json.get("results") or []
    if not isinstance(results, list) or len(results) == 0:
        raise HTTPException(status_code=502, detail="PlantNet nem adott találatot.")

    top_matches: List[Match] = []
    for r in results[:5]:
        score = float(r.get("score") or 0.0)
        species = r.get("species") or {}
        sci = species.get("scientificNameWithoutAuthor") or species.get("scientificName") or "Unknown"
        top_matches.append(Match(name=str(sci), score=score))

    best = top_matches[0].name
    top1 = float(top_matches[0].score)

    # "level" is not directly provided by PlantNet; we keep "species" as a stable label
    conf = Confidence(top1_score=top1, level="species")

    return IdentifyResponse(bestMatch=best, confidence=conf, topMatches=top_matches)


# ----------------------------
# Routes
# ----------------------------
@app.get("/")
def rootGet():
    return {"status": "ok"}


@app.get("/health")
def healthGet():
    return {"status": "ok"}


@app.post("/identify", response_model=IdentifyResponse)
async def identifyPlant(
    image: UploadFile = File(...),
    organs: str = Form(DEFAULT_ORGANS),
):
    if not image:
        raise HTTPException(status_code=422, detail="Hiányzik az image mező.")

    if image.content_type not in ALLOWED_CT:
        raise HTTPException(status_code=422, detail=f"Unsupported content type: {image.content_type}")

    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=422, detail="Üres fájl.")

    pn = await _call_plantnet(
        image_bytes=image_bytes,
        filename=image.filename or "image.jpg",
        content_type=image.content_type,
        organs=(organs or DEFAULT_ORGANS),
    )
    return _to_identify_response(pn)


@app.post("/identify_b64", response_model=IdentifyResponse)
async def identifyPlantB64(payload: IdentifyB64Request):
    raw = payload.image_base64 or ""
    organs = payload.organs or DEFAULT_ORGANS

    # Content-type guess from dataurl header (if present)
    guessed_ct = _guess_content_type_from_header(raw)

    b64 = _clean_data_url(raw)

    try:
        # validate=True -> strict base64 (fixes many "padding" surprises)
        image_bytes = base64.b64decode(b64, validate=True)
    except Exception:
        raise HTTPException(status_code=422, detail="Nem érvényes base64: Incorrect padding vagy hibás karakter.")

    if not image_bytes:
        raise HTTPException(status_code=422, detail="Üres base64 tartalom.")

    # If not guessed, assume jpeg (works for most cameras)
    content_type = guessed_ct or "image/jpeg"
    if content_type not in ALLOWED_CT:
        raise HTTPException(status_code=422, detail=f"Unsupported content type: {content_type}")

    pn = await _call_plantnet(
        image_bytes=image_bytes,
        filename="image.jpg" if content_type == "image/jpeg" else "image.png",
        content_type=content_type,
        organs=organs,
    )
    return _to_identify_response(pn)
```0
