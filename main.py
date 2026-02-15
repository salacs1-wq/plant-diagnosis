# main.py
import os
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_IDENTIFY_URL = "https://my-api.plantnet.org/v2/identify/all"

app = FastAPI(
    title="Plant Diagnosis API (PlantNet proxy)",
    version="2.0.0",
    description="PlantNet alapú növényazonosítás GPT Actions kompatibilis (openaiFileIdRefs) + opcionális multipart teszt endpoint.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Models (GPT Actions) ----------------------------------------------------

class IdentifyActionRequest(BaseModel):
    # OpenAI Actions fájl-küldési konvenció: openaiFileIdRefs
    # A docs szerint schema-ban lehet string[], de runtime-ban objektumok jönnek (id, name, mime_type, download_link).
    openaiFileIdRefs: List[Any] = Field(..., description="Files from conversation; runtime objects include download_link.")
    organs: List[str] = Field(default_factory=lambda: ["leaf"], description="Pl.: leaf, flower, fruit, bark...")

# --- Helpers ----------------------------------------------------------------

def _require_api_key() -> None:
    if not PLANTNET_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Hiányzik a PLANTNET_API_KEY környezeti változó a Renderen.",
        )

def _download_openai_file(openai_file_obj: Any) -> tuple[str, bytes, str]:
    """
    openai_file_obj tipikusan:
      {"name": "...", "id": "...", "mime_type": "image/jpeg", "download_link": "https://files.oaiusercontent.com/...."}
    """
    if not isinstance(openai_file_obj, dict):
        raise HTTPException(status_code=400, detail="openaiFileIdRefs elem nem objektum (dict).")

    url = openai_file_obj.get("download_link")
    name = openai_file_obj.get("name") or "image.jpg"
    mime = openai_file_obj.get("mime_type") or "image/jpeg"

    if not url or not isinstance(url, str):
        raise HTTPException(status_code=400, detail="Hiányzik a download_link az openaiFileIdRefs objektumból.")

    # Actions-ben a link ~5 percig él
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        content = r.content
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Nem sikerült letölteni az OpenAI fájlt: {e}")

    if not content or len(content) < 200:
        raise HTTPException(status_code=400, detail="A letöltött fájl üres vagy túl kicsi.")

    return name, content, mime

def _call_plantnet(image_name: str, img_bytes: bytes, mime_type: str, organs: List[str]) -> Dict[str, Any]:
    _require_api_key()

    files = {
        # PlantNet: images mező (több kép is lehet)
        "images": (image_name, img_bytes, mime_type or "image/jpeg")
    }

    params = {"api-key": PLANTNET_API_KEY}

    # Itt csak azt küldjük, ami biztosan elfogadott.
    # A korábbi hibáid alapján a PlantNet egyes mezőket "not allowed"-dal dobott (pl. language, includeRelatedImages).
    data = []
    for o in organs or ["leaf"]:
        data.append(("organs", o))

    try:
        resp = requests.post(
            PLANTNET_IDENTIFY_URL,
            params=params,
            files=files,
            data=data,       # organs=leaf&organs=...
            timeout=60,
        )
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"PlantNet hálózati hiba: {e}")

    if resp.status_code in (401, 403):
        raise HTTPException(
            status_code=502,
            detail=f"PlantNet jogosultsági hiba ({resp.status_code}). Ellenőrizd az API kulcsot / csomagot.",
        )

    if resp.status_code >= 400:
        try:
            err_json = resp.json()
        except Exception:
            err_json = {"raw": resp.text}
        raise HTTPException(
            status_code=502,
            detail={"plantnet_status": resp.status_code, "plantnet_error": err_json},
        )

    try:
        return resp.json()
    except Exception:
        raise HTTPException(status_code=502, detail="PlantNet nem JSON választ adott (váratlan).")

def _simplify(out: Dict[str, Any], organs: List[str]) -> Dict[str, Any]:
    results = out.get("results", []) or []
    top = results[:3]

    simplified_top = []
    for item in top:
        sp = item.get("species") or {}
        fam = (sp.get("family") or {})
        simplified_top.append(
            {
                "score": item.get("score"),
                "scientificName": sp.get("scientificName"),
                "scientificNameWithoutAuthor": sp.get("scientificNameWithoutAuthor"),
                "family": fam.get("scientificNameWithoutAuthor") or fam.get("scientificName"),
                "commonNames": sp.get("commonNames") or [],
            }
        )

    top1 = simplified_top[0]["score"] if len(simplified_top) > 0 else None
    top2 = simplified_top[1]["score"] if len(simplified_top) > 1 else None
    gap = (top1 - top2) if (top1 is not None and top2 is not None) else None

    level = "alacsony"
    if top1 is not None:
        if top1 >= 0.7:
            level = "magas"
        elif top1 >= 0.4:
            level = "közepes"

    best_match = out.get("bestMatch")
    if not best_match and simplified_top:
        best_match = simplified_top[0].get("scientificName")

    return {
        "bestMatch": best_match,
        "confidence": {
            "top1_score": top1,
            "level": level,
            "top1_top2_gap": gap,
        },
        "topMatches": simplified_top,
        "meta": {"organs": organs},
        "raw": out,
    }


# --- Routes -----------------------------------------------------------------

@app.get("/")
def health():
    return {"status": "ok", "message": "Növénydiagnosztikai API fut"}

# Render / load balancer néha HEAD-et küld -> ne legyen 405
@app.head("/")
def health_head():
    return JSONResponse({"status": "ok"})

# ✅ GPT Actions kompatibilis: JSON body + openaiFileIdRefs
@app.post("/identify")
def identify_action(payload: IdentifyActionRequest):
    if not payload.openaiFileIdRefs or len(payload.openaiFileIdRefs) == 0:
        raise HTTPException(status_code=400, detail="openaiFileIdRefs üres (nincs kép).")

    # Első fájlt használjuk (ha több lesz, később bővíthető)
    image_name, img_bytes, mime_type = _download_openai_file(payload.openaiFileIdRefs[0])

    out = _call_plantnet(
        image_name=image_name,
        img_bytes=img_bytes,
        mime_type=mime_type,
        organs=payload.organs,
    )
    return JSONResponse(_simplify(out, payload.organs))

# ✅ Hoppscotch / manuális teszt: multipart/form-data (image mezővel)
@app.post("/identify_multipart")
async def identify_multipart(
    image: UploadFile = File(..., description="A feltöltött kép (JPG/PNG)."),
    organs: List[str] = Form(default=["leaf"]),
):
    _require_api_key()

    try:
        img_bytes = await image.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Nem tudtam beolvasni a képfájlt: {e}")

    if not img_bytes or len(img_bytes) < 200:
        raise HTTPException(status_code=400, detail="Üres vagy túl kicsi képfájl érkezett.")

    out = _call_plantnet(
        image_name=image.filename or "image.jpg",
        img_bytes=img_bytes,
        mime_type=image.content_type or "image/jpeg",
        organs=organs,
    )
    return JSONResponse(_simplify(out, organs))
