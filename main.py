import os
import re
import base64
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, File, UploadFile, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


# ----------------------------
# Config
# ----------------------------
PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_PROJECT = os.getenv("PLANTNET_PROJECT", "weurope").strip() # e.g. weurope or all
PLANTNET_BASE_URL = os.getenv("PLANTNET_BASE_URL", "https://my-api.plantnet.org").strip()

# PlantNet identify endpoint (v2)
# https://my-api.plantnet.org/v2/identify/{project}?api-key=...
def plantnet_identify_url(project: str) -> str:
    project = (project or PLANTNET_PROJECT).strip()
    return f"{PLANTNET_BASE_URL}/v2/identify/{project}"


# ----------------------------
# FastAPI
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


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


# ----------------------------
# Models
# ----------------------------
class IdentifyB64Request(BaseModel):
    image_base64: str
    organs: str = "leaf"
    project: Optional[str] = None


# ----------------------------
# Helpers
# ----------------------------
DATA_URL_RE = re.compile(r"^data:(image\/[a-zA-Z0-9.+-]+);base64,(.*)$", re.DOTALL)


def decode_base64_image(image_base64: str) -> tuple[bytes, str]:
    """
    Accepts:
      - pure base64 string
      - data URL: data:image/jpeg;base64,....
    Returns: (bytes, content_type)
    """
    s = (image_base64 or "").strip()

    content_type = "image/jpeg"
    m = DATA_URL_RE.match(s)
    if m:
        content_type = m.group(1).strip()
        s = m.group(2).strip()

    # Fix missing padding (common cause of "Incorrect padding")
    missing = (-len(s)) % 4
    if missing:
        s += "=" * missing

    try:
        raw = base64.b64decode(s, validate=False)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Nem érvényes base64: {e}")

    if not raw:
        raise HTTPException(status_code=422, detail="Üres base64 tartalom.")

    return raw, content_type


async def call_plantnet_identify(
    image_bytes: bytes,
    filename: str,
    content_type: str,
    organs: str,
    project: Optional[str] = None,
) -> Dict[str, Any]:
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="Hiányzik a PLANTNET_API_KEY környezeti változó a szerveren.")

    url = plantnet_identify_url(project or PLANTNET_PROJECT)
    params = {"api-key": PLANTNET_API_KEY}

    # PlantNet v2 expects multipart:
    # - files: images (can be multiple; same field name repeated)
    # - form fields: organs (can be repeated; for 1 image it's a single value)
    files = {
        "images": (filename or "image.jpg", image_bytes, content_type or "image/jpeg")
    }
    data = {
        "organs": organs or "leaf"
    }

    timeout = httpx.Timeout(60.0, connect=20.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, params=params, files=files, data=data)

    # If PlantNet returns non-JSON, keep text
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text}

    if r.status_code >= 400:
        return {
            "detail": {
                "plantnet_status": r.status_code,
                "plantnet_error": body,
                "called_url": str(r.request.url),
            }
        }

    return body


# ----------------------------
# Endpoints
# ----------------------------
@app.post("/identify")
async def identify(
    image: UploadFile = File(...),
    organs: str = Query("leaf", description="leaf/flower/fruit/bark"),
    project: Optional[str] = Query(None, description="Pl. weurope vagy all"),
) -> Dict[str, Any]:
    """
    Egy kép alapján növényazonosítás PlantNet v2-vel.
    """
    # Read bytes
    content = await image.read()
    if not content:
        raise HTTPException(status_code=422, detail="Üres fájl.")

    result = await call_plantnet_identify(
        image_bytes=content,
        filename=image.filename or "image.jpg",
        content_type=image.content_type or "image/jpeg",
        organs=organs,
        project=project,
    )
    return result


@app.post("/identify_b64")
async def identify_b64(payload: IdentifyB64Request) -> Dict[str, Any]:
    """
    Base64 string alapján növényazonosítás PlantNet v2-vel.
    """
    img_bytes, content_type = decode_base64_image(payload.image_base64)

    result = await call_plantnet_identify(
        image_bytes=img_bytes,
        filename="image.jpg",
        content_type=content_type,
        organs=payload.organs,
        project=payload.project,
    )
    return result
