import os
from typing import List, Optional

import requests
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()

# PlantNet base endpoint (My Pl@ntNet API)
PLANTNET_IDENTIFY_URL = "https://my-api.plantnet.org/v2/identify/all"

app = FastAPI(
    title="Plant Diagnosis API (PlantNet proxy)",
    version="1.0.1",
    description="PlantNet alapú növényazonosítás kép-feltöltéssel (proxy).",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def health():
    return {"status": "ok", "message": "Növénydiagnosztikai API fut"}


@app.post("/identify")
async def identify(
    # FONTOS: a mező neve pontosan "image"
    image: UploadFile = File(..., description="A feltöltött kép (JPG/PNG)."),
    # organs listaként jön (pl. leaf), ezt mi jól fogjuk továbbítani
    organs: List[str] = Form(default=["leaf"], description="Pl.: leaf, flower, fruit, bark..."),
    # elfogadjuk, de NEM küldjük PlantNet felé (mert ott nem mindig támogatott)
    language: Optional[str] = Form(default=None, description="Pl.: en, hu (nem kerül továbbításra PlantNet felé)"),
    includeRelatedImages: bool = Form(default=False),
    noReject: bool = Form(default=False),
):
    if not PLANTNET_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Hiányzik a PLANTNET_API_KEY környezeti változó a Renderen.",
        )

    try:
        img_bytes = await image.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Nem tudtam beolvasni a képfájlt: {e}")

    if not img_bytes or len(img_bytes) < 200:
        raise HTTPException(status_code=400, detail="Üres vagy túl kicsi képfájl érkezett.")

    # ✅ PlantNet: multipart (images + organs mezők ismételve)
    files = [
        ("images", (image.filename or "image.jpg", img_bytes, image.content_type or "image/jpeg"))
    ]

    # ✅ organs: így kell küldeni, hogy organs=leaf&organs=flower...
data = []
for o in organs:
    data.append(("organs", o))

    # ✅ api-key query param
    params = {"api-key": PLANTNET_API_KEY}

    try:
        r = requests.post(
            PLANTNET_IDENTIFY_URL,
            params=params,
            data=data,
            files=files,
            timeout=60,
        )
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"PlantNet hálózati hiba: {e}")

    if r.status_code in (401, 403):
        raise HTTPException(
            status_code=502,
            detail=f"PlantNet jogosultsági hiba ({r.status_code}). Ellenőrizd az API kulcsot / korlátozásokat.",
        )

    if r.status_code >= 400:
        try:
            err_json = r.json()
        except Exception:
            err_json = {"raw": r.text}
        raise HTTPException(
            status_code=502,
            detail={"plantnet_status": r.status_code, "plantnet_error": err_json},
        )

    try:
        out = r.json()
    except Exception:
        raise HTTPException(status_code=502, detail="PlantNet nem JSON választ adott (váratlan).")

    results = out.get("results", []) or []
    top = results[:3]

    def _pick_common_names(item):
        sp = (item.get("species") or {})
        return sp.get("commonNames") or []

    def _pick_family(item):
        sp = (item.get("species") or {})
        fam = sp.get("family") or {}
        return fam.get("scientificNameWithoutAuthor") or fam.get("scientificName") or None

    simplified_top = []
    for item in top:
        sp = item.get("species") or {}
        simplified_top.append(
            {
                "score": item.get("score"),
                "scientificName": sp.get("scientificName"),
                "scientificNameWithoutAuthor": sp.get("scientificNameWithoutAuthor"),
                "family": _pick_family(item),
                "commonNames": _pick_common_names(item),
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
        else:
            level = "alacsony"

    best_match = out.get("bestMatch")
    if not best_match and simplified_top:
        best_match = simplified_top[0].get("scientificName")

    return JSONResponse(
        {
            "bestMatch": best_match,
            "confidence": {
                "top1_score": top1,
                "level": level,
                "top1_top2_gap": gap,
            },
            "topMatches": simplified_top,
            "meta": {
                "organs": organs,
                "language": language,  # csak info, NEM megy PlantNetnek
            },
            "raw": out,
        }
    )
