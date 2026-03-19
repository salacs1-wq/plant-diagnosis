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
    caseType: str  # weed / disease / pest


# =========================
# VALIDATION
# =========================
def validate_request(req: DiagnoseRequest) -> None:
    if req.project != "k-middle-europe":
        raise ValueError("A project mezőnek 'k-middle-europe' értékűnek kell lennie.")

    if req.mode != "expert":
        raise ValueError("A mode mezőnek 'expert' értékűnek kell lennie.")

    if req.caseType not in ["weed", "disease", "pest"]:
        raise ValueError("A caseType csak 'weed', 'disease' vagy 'pest' lehet.")


# =========================
# FILE DOWNLOAD
# =========================
def get_download_link(file_refs: List[OpenAIFileRef]) -> str:
    if not file_refs:
        raise ValueError("Nincs feltöltött fájl.")

    first_file = file_refs[0]

    if not first_file.download_link:
        raise ValueError("A feltöltött fájlhoz nem érkezett download_link.")

    return first_file.download_link


def download_image(download_link: str) -> bytes:
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(download_link, headers=headers, timeout=30)
    response.raise_for_status()
    return response.content


# =========================
# PLANTNET CALLS
# =========================
def call_plantnet_identify(image_bytes: bytes) -> Dict[str, Any]:
    if not PLANTNET_API_KEY:
        raise ValueError("Hiányzik a PLANTNET_API_KEY környezeti változó.")

    url = "https://my-api.plantnet.org/v2/identify"
    params = {
        "api-key": PLANTNET_API_KEY,
        "project": "k-middle-europe",
        "include-related-images": "false"
    }

    files = {"images": ("image.jpg", image_bytes, "image/jpeg")}
    data = {"organs": "leaf"}

    response = requests.post(
        url,
        params=params,
        files=files,
        data=data,
        timeout=60
    )
    response.raise_for_status()
    return response.json()


def call_plantnet_diseases(image_bytes: bytes) -> Dict[str, Any]:
    if not PLANTNET_API_KEY:
        raise ValueError("Hiányzik a PLANTNET_API_KEY környezeti változó.")

    url = "https://my-api.plantnet.org/v2/diseases/identify"
    params = {"api-key": PLANTNET_API_KEY}

    files = {"images": ("image.jpg", image_bytes, "image/jpeg")}
    data = {"organs": "leaf"}

    response = requests.post(
        url,
        params=params,
        files=files,
        data=data,
        timeout=60
    )
    response.raise_for_status()
    return response.json()


# =========================
# SAFE WRAPPERS
# =========================
def safe_identify(image_bytes: bytes) -> Dict[str, Any]:
    try:
        return call_plantnet_identify(image_bytes)
    except Exception as e:
        return {
            "error": True,
            "message": str(e),
            "results": []
        }


def safe_disease(image_bytes: bytes) -> Dict[str, Any]:
    try:
        return call_plantnet_diseases(image_bytes)
    except Exception as e:
        return {
            "error": True,
            "message": str(e),
            "results": []
        }


# =========================
# FORMAT HELPERS
# =========================
def pick_hungarian_name(species: Dict[str, Any]) -> Optional[str]:
    common_names = species.get("commonNames", [])
    if common_names and isinstance(common_names, list):
        return common_names[0]
    return None


def normalize_score(item: Dict[str, Any]) -> float:
    try:
        return round(float(item.get("score", 0)) * 100, 1)
    except Exception:
        return 0.0


def format_weed_top5(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    formatted = []

    for idx, item in enumerate(results[:5], start=1):
        species = item.get("species", {})
        formatted.append({
            "rank": idx,
            "latin_name": species.get("scientificNameWithoutAuthor", "ismeretlen"),
            "hungarian_name": pick_hungarian_name(species),
            "score": normalize_score(item)
        })

    return formatted


def format_crop_top5(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    formatted = []

    for idx, item in enumerate(results[:5], start=1):
        species = item.get("species", {})
        formatted.append({
            "rank": idx,
            "latin_name": species.get("scientificNameWithoutAuthor", "ismeretlen"),
            "hungarian_name": pick_hungarian_name(species),
            "score": normalize_score(item)
        })

    return formatted


def format_disease_pest_list(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    formatted = []

    for idx, item in enumerate(results[:10], start=1):
        species = item.get("species", {})
        formatted.append({
            "rank": idx,
            "latin_name": species.get("scientificNameWithoutAuthor", "ismeretlen"),
            "hungarian_name": pick_hungarian_name(species),
            "score": normalize_score(item)
        })

    return formatted


# =========================
# ROUTES
# =========================
@app.post("/diagnose")
async def diagnose(req: DiagnoseRequest):
    try:
        validate_request(req)

        if req.caseType != "weed":
            return {
                "status": "error",
                "mode": req.caseType,
                "message": "A /diagnose végpont csak gyom módhoz használható."
            }

        download_link = get_download_link(req.openaiFileIdRefs)
        image_bytes = download_image(download_link)

        plantnet_response = safe_identify(image_bytes)
        results = plantnet_response.get("results", [])

        top5 = format_weed_top5(results)

        return {
            "status": "success",
            "mode": "weed",
            "top_match": top5[0] if top5 else None,
            "top5": top5
        }

    except Exception as e:
        return {
            "status": "error",
            "mode": req.caseType,
            "message": str(e)
        }


@app.post("/diagnose-dp")
async def diagnose_disease_pest(req: DiagnoseRequest):
    try:
        validate_request(req)

        if req.caseType not in ["disease", "pest"]:
            return {
                "status": "error",
                "mode": req.caseType,
                "message": "A /diagnose-dp végpont csak betegség vagy kártevő módhoz használható."
            }

        download_link = get_download_link(req.openaiFileIdRefs)
        image_bytes = download_image(download_link)

        identify_response = safe_identify(image_bytes)
        dp_response = safe_disease(image_bytes)

        crop_top5 = format_crop_top5(identify_response.get("results", []))
        dp_list = format_disease_pest_list(dp_response.get("results", []))

        return {
            "status": "success",
            "mode": req.caseType,
            "crop_top5": crop_top5,
            "diseases_and_pests_top": dp_list
        }

    except Exception as e:
        return {
            "status": "error",
            "mode": req.caseType,
            "message": str(e)
        }


@app.get("/")
def root():
    return {
        "status": "ok",
        "version": "stable-reset"
    }
