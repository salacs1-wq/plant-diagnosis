# main.py
from __future__ import annotations

import os
import io
import time
from typing import Optional, List, Literal, Dict, Any

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

try:
    from PIL import Image  # pillow
except Exception:  # pragma: no cover
    Image = None  # type: ignore


APP_NAME = "plant-diagnosis"
APP_VERSION = "1.0.0"

Mode = Literal["weed", "disease", "pest", "crop", "auto"]

app = FastAPI(
    title="Plant Diagnosis API",
    version=APP_VERSION,
    description="Határszemle / növényorvosi asszisztens – diagnosztikai API (Render kompatibilis).",
)

# CORS – ha a GPT / web kliens más doménről hívja
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ha akarod, szűkíthető
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["meta"])
def root() -> Dict[str, Any]:
    return {
        "name": APP_NAME,
        "version": APP_VERSION,
        "status": "ok",
        "endpoints": ["/health", "/v1/diagnose", "/openapi.json", "/docs"],
    }


@app.get("/health", tags=["meta"])
def health() -> Dict[str, Any]:
    # Render healthcheck-hez tökéletes
    return {"status": "ok", "ts": int(time.time())}


def _read_image_bytes(file: UploadFile) -> bytes:
    if not file:
        raise HTTPException(status_code=400, detail="Hiányzik a fájl (image).")
    return file.file.read()


def _basic_image_info(image_bytes: bytes) -> Dict[str, Any]:
    """
    Csak meta infó: segít debugolni (felbontás, formátum).
    Nem kötelező a működéshez.
    """
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


def run_inference(mode: Mode, image_bytes: bytes) -> Dict[str, Any]:
    """
    Itt kell majd a TE diagnosztikád (modell / külső API / OpenAI Vision stb.).
    Most szándékosan úgy van megírva, hogy kulcs nélkül is fusson (ne dőljön el a deploy).
    """
    # Példa: környezeti változóval vezérelhető "kulcs" (ha később kell)
    api_key = os.getenv("DIAG_API_KEY") or os.getenv("OPENAI_API_KEY") or ""

    # Ha nincs semmi bekötve, adjunk vissza egy stabil, tesztelhető választ:
    base = {
        "mode": mode,
        "engine": "stub",
        "has_api_key": bool(api_key),
        "candidates": [
            {
                "label": "unknown",
                "confidence": 0.01,
                "notes": "Nincs bekötve modell/API. Cseréld a run_inference() függvényt a saját logikádra.",
            }
        ],
    }

    # Ide jöhet később a valódi inference
    # pl. return your_model.predict(...)
    return base


@app.post("/v1/diagnose", tags=["diagnosis"])
async def diagnose(
    mode: Mode = Form("auto"),
    image: UploadFile = File(...),
    client_id: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
    debug: bool = Form(False),
) -> JSONResponse:
    """
    Multipart/form-data:
      - mode: weed | disease | pest | crop | auto
      - image: feltöltött kép
      - client_id: opcionális
      - note: opcionális
      - debug: true/false (ha true, képinformációkat is visszaad)
    """
    if mode not in ("weed", "disease", "pest", "crop", "auto"):
        raise HTTPException(status_code=400, detail="Érvénytelen mode.")

    image_bytes = _read_image_bytes(image)
    if len(image_bytes) < 50:
        raise HTTPException(status_code=400, detail="Túl kicsi / üres kép.")

    result = run_inference(mode, image_bytes)

    payload: Dict[str, Any] = {
        "ok": True,
        "request": {
            "mode": mode,
            "filename": image.filename,
            "content_type": image.content_type,
            "client_id": client_id,
            "note": note,
        },
        "result": result,
    }

    if debug:
        payload["debug"] = _basic_image_info(image_bytes)

    return JSONResponse(payload)


# Render/uvicorn belépési pont:
# Start command (Render):  uvicorn main:app --host 0.0.0.0 --port $PORT
