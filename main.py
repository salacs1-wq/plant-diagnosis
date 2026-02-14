import os
import requests
from fastapi import FastAPI, File, UploadFile, HTTPException, Query

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_BASE = "https://my-api.plantnet.org/v2/identify"

app = FastAPI()

@app.get("/")
def home():
    return {"status": "ok", "message": "Plant diagnosis API running"}

def confidence_label(score: float) -> str:
    # Egyszerű, terepi címkék
    if score >= 0.75:
        return "magas"
    if score >= 0.45:
        return "közepes"
    return "alacsony"

def next_photos_hint(best_scientific: str, top2_gap: float) -> list[str]:
    # Általános, hasznos fotóigények (később kultúra/gyomcsoport szerint finomítható)
    hints = [
        "teljes növény (tő + levelek) közelről",
        "tőlevélrózsa / szártő részlete",
        "ha van: virág/termés közelről",
    ]
    # Ha nagyon bizonytalan (kicsi különbség a top1-top2 között), kérj több döntő részletet
    if top2_gap < 0.10:
        hints.append("levélalak és levélszél közelről (fogazottság, karéjok)")
    # Capsella esetén külön megjegyzés
    if "Capsella" in (best_scientific or ""):
        hints.append("ha lehet: táskás becő (pásztortáska termése) közelről")
    return hints

@app.post("/identify")
async def identify(
    image: UploadFile = File(...),
    organs: str = Query("leaf", description="Organ hint for PlantNet (leaf/flower/fruit/bark/auto)."),
    project: str = Query("all", description="PlantNet project, usually 'all'."),
    top_k: int = Query(3, ge=1, le=10, description="How many top matches to return in short output."),
    raw: bool = Query(False, description="If true, include full PlantNet raw response under 'raw'.")
):
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="PLANTNET_API_KEY nincs beállítva a szerveren.")

    image_bytes = await image.read()

    url = f"{PLANTNET_BASE}/{project}"
    params = {"api-key": PLANTNET_API_KEY}
    files = {"images": ("image.jpg", image_bytes, image.content_type or "image/jpeg")}
    data = {"organs": organs}

    try:
        r = requests.post(url, params=params, files=files, data=data, timeout=60)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"PlantNet kapcsolat hiba: {str(e)}")

    if r.status_code != 200:
        # PlantNet hiba szövegét add vissza diagnosztikához
        raise HTTPException(status_code=500, detail=f"PlantNet hiba: {r.text}")

    plantnet = r.json()

    results = plantnet.get("results") or []
    best_match = plantnet.get("bestMatch")

    # Top találatok rövidítése
    top = []
    for item in results[:top_k]:
        sp = (item.get("species") or {})
        top.append({
            "score": round(float(item.get("score") or 0.0), 5),
            "scientificName": sp.get("scientificName") or sp.get("scientificNameWithoutAuthor"),
            "scientificNameWithoutAuthor": sp.get("scientificNameWithoutAuthor"),
            "family": (sp.get("family") or {}).get("scientificNameWithoutAuthor"),
            "commonNames": sp.get("commonNames") or [],
        })

    top1 = float(results[0].get("score") or 0.0) if len(results) >= 1 else 0.0
    top2 = float(results[1].get("score") or 0.0) if len(results) >= 2 else 0.0
    gap = top1 - top2

    response = {
        "bestMatch": best_match,
        "confidence": {
            "top1_score": round(top1, 5),
            "level": confidence_label(top1),
            "top1_top2_gap": round(gap, 5)
        },
        "topMatches": top,
        "nextPhotos": next_photos_hint(best_match or "", gap),
        "meta": {
            "project": plantnet.get("query", {}).get("project", project),
            "organs": plantnet.get("query", {}).get("organs", [organs]),
            "language": plantnet.get("language")
        }
    }

    if raw:
        response["raw"] = plantnet

    return response
