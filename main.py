from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional, Any, Dict
import requests
import os

app = FastAPI()

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY")


# =========================
# MODELS
# =========================
class OpenAIFileRef(BaseModel):
    id: str
    name: Optional[str] = None
    mime_type: Optional[str] = None
    download_link: Optional[str] = None


class DiagnoseRequest(BaseModel):
    openaiFileIdRefs: List[OpenAIFileRef]
    project: str
    mode: str
    caseType: str


# =========================
# HELPERS
# =========================
def get_download_link(file_refs: List[OpenAIFileRef]) -> str:
    if not file_refs:
        raise ValueError("Nincs feltöltött fájl.")

    first_file = file_refs[0]

    if not first_file.download_link:
        raise ValueError("A feltöltött fájlhoz nem érkezett download_link.")

    return first_file.download_link


def download_image(download_link: str) -> bytes:
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    response = requests.get(download_link, headers=headers, timeout=30)
    response.raise_for_status()
    return response.content


def call_plantnet(image_bytes: bytes) -> Dict[str, Any]:
    if not PLANTNET_API_KEY:
        raise ValueError("Hiányzik a PLANTNET_API_KEY környezeti változó.")

    url = f"https://my-api.plantnet.org/v2/identify/all?api-key={PLANTNET_API_KEY}"

    files = {
        "images": ("image.jpg", image_bytes, "image/jpeg")
    }

    data = {
        "organs": "leaf"
    }

    response = requests.post(url, files=files, data=data, timeout=60)
    response.raise_for_status()
    return response.json()


def pick_hungarian_name(species: Dict[str, Any]) -> Optional[str]:
    common_names = species.get("commonNames", [])
    if common_names and isinstance(common_names, list):
        return common_names[0]
    return None


def format_top5(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    formatted = []

    for idx, item in enumerate(results[:5], start=1):
        species = item.get("species", {})
        formatted.append({
            "rank": idx,
            "latin_name": species.get("scientificNameWithoutAuthor", "ismeretlen"),
            "hungarian_name": pick_hungarian_name(species),
            "score": round(item.get("score", 0), 5)
        })

    return formatted


def build_mode_assessment(case_type: str, top5: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not top5:
        return {
            "summary": "Nincs értékelhető találat.",
            "confidence": "nincs",
            "note": "A kép alapján nem érkezett használható PlantNet találat."
        }

    top1 = top5[0]
    score = top1["score"]

    if score >= 0.50:
        confidence = "közepes vagy jobb"
    elif score >= 0.30:
        confidence = "bizonytalan"
    else:
        confidence = "gyenge"

    if case_type == "weed":
        summary = "Gyom mód: a találatok növényazonosítási eredmények, ezek alapján lehet gyomértékelést végezni."
        note = "A gyom státuszt a GPT a TOP5 és a képi megjelenés alapján értelmezze."
    elif case_type == "disease":
        summary = "Betegség mód: a PlantNet elsődlegesen a növényt azonosítja, nem magát a betegséget."
        note = "A GPT a növényazonosítást használja alapnak, és a képi tüneteket külön értelmezi."
    elif case_type == "pest":
        summary = "Kártevő mód: a PlantNet elsődlegesen a gazdanövényt azonosítja, nem közvetlenül a kártevőt."
        note = "A GPT a növényazonosítást használja alapnak, és a kárképet külön értelmezi."
    else:
        summary = "Ismeretlen mód."
        note = "A caseType értéke nem támogatott."

    return {
        "summary": summary,
        "confidence": confidence,
        "note": note
    }


def validate_request(req: DiagnoseRequest) -> None:
    if req.project != "k-middle-europe":
        raise ValueError("A project mezőnek 'k-middle-europe' értékűnek kell lennie.")

    if req.mode != "expert":
        raise ValueError("A mode mezőnek 'expert' értékűnek kell lennie.")

    if req.caseType not in ["weed", "disease", "pest"]:
        raise ValueError("A caseType csak 'weed', 'disease' vagy 'pest' lehet.")


# =========================
# ROUTES
# =========================
@app.post("/diagnose")
async def diagnose(req: DiagnoseRequest):
    try:
        validate_request(req)

        download_link = get_download_link(req.openaiFileIdRefs)
        image_bytes = download_image(download_link)

        plantnet_response = call_plantnet(image_bytes)
        results = plantnet_response.get("results", [])
        top5 = format_top5(results)

        return {
            "status": "success",
            "mode": req.caseType,
            "message": "Sikeres diagnózis",
            "top_match": top5[0] if top5 else None,
            "top5": top5,
            "mode_assessment": build_mode_assessment(req.caseType, top5),
            "crop_match": None,
            "raw_count": len(results)
        }

    except Exception as e:
        return {
            "status": "error",
            "mode": req.caseType if hasattr(req, "caseType") else None,
            "message": str(e)
        }


@app.get("/")
def root():
    return {
        "status": "ok",
        "version": "stable-gpt-diagnose-v1"
    }
