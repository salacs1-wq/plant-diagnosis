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

    url = "https://my-api.plantnet.org/v2/identify/all"
    params = {"api-key": PLANTNET_API_KEY}
    files = {"images": ("image.jpg", image_bytes, "image/jpeg")}
    data = {"organs": "leaf"}

    response = requests.post(url, params=params, files=files, data=data, timeout=60)
    response.raise_for_status()
    return response.json()


def call_plantnet_diseases(image_bytes: bytes) -> Dict[str, Any]:
    if not PLANTNET_API_KEY:
        raise ValueError("Hiányzik a PLANTNET_API_KEY környezeti változó.")

    url = "https://my-api.plantnet.org/v2/diseases/identify"
    params = {"api-key": PLANTNET_API_KEY}
    files = {"images": ("image.jpg", image_bytes, "image/jpeg")}
    data = {"organs": "leaf"}

    response = requests.post(url, params=params, files=files, data=data, timeout=60)
    response.raise_for_status()
    return response.json()


# =========================
# INAT CALL (ÚJ)
# =========================
def call_inat(image_bytes: bytes) -> Dict[str, Any]:
    url = "https://api.inaturalist.org/v1/computervision/score_image"

    # NÖVÉNY
    plant_res = requests.post(
        url,
        files={"image": ("image.jpg", image_bytes, "image/jpeg")},
        data={"taxon_id": 47126},  # Plantae
        timeout=60
    ).json()

    # ROVAR
    insect_res = requests.post(
        url,
        files={"image": ("image.jpg", image_bytes, "image/jpeg")},
        data={"taxon_id": 47158},  # Insecta
        timeout=60
    ).json()

    return {
        "plant": plant_res.get("results", [])[:3],
        "insect": insect_res.get("results", [])[:3]
    }


# =========================
# FORMAT HELPERS
# =========================
def pick_hungarian_name(species: Dict[str, Any]) -> Optional[str]:
    common_names = species.get("commonNames", [])
    if common_names and isinstance(common_names, list):
        return common_names[0]
    return None


def first_nonempty_str(values: List[Any]) -> Optional[str]:
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def normalize_score(item: Dict[str, Any]) -> float:
    raw = item.get("score", item.get("probability", 0))
    try:
        return round(float(raw), 5)
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
            "score": round(item.get("score", 0), 5)
        })

    return formatted


def format_crop_top5(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    formatted = []

    for idx, item in enumerate(results[:5], start=1):
        species = item.get("species", {}) if isinstance(item.get("species"), dict) else {}
        formatted.append({
            "rank": idx,
            "latin_name": first_nonempty_str([
                item.get("scientificName"),
                item.get("scientificNameWithoutAuthor"),
                species.get("scientificNameWithoutAuthor"),
                item.get("name"),
                item.get("label"),
                "ismeretlen"
            ]),
            "hungarian_name": first_nonempty_str([
                item.get("common_name"),
                item.get("commonName"),
                pick_hungarian_name(species)
            ]),
            "score": normalize_score(item)
        })

    return formatted


def format_disease_pest_list(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    formatted = []

    for idx, item in enumerate(results[:10], start=1):
        species = item.get("species", {}) if isinstance(item.get("species"), dict) else {}
        formatted.append({
            "rank": idx,
            "latin_name": first_nonempty_str([
                item.get("scientificName"),
                item.get("scientificNameWithoutAuthor"),
                species.get("scientificNameWithoutAuthor"),
                item.get("name"),
                item.get("title"),
                item.get("label"),
                "ismeretlen"
            ]),
            "hungarian_name": first_nonempty_str([
                item.get("common_name"),
                item.get("commonName"),
                pick_hungarian_name(species)
            ]),
            "score": normalize_score(item),
            "raw_item": item
        })

    return formatted


# =========================
# ROUTES
# =========================
@app.post("/diagnose-dp")
async def diagnose_disease_pest(req: DiagnoseRequest):
    try:
        validate_request(req)

        download_link = get_download_link(req.openaiFileIdRefs)
        image_bytes = download_image(download_link)

        dp_response = call_plantnet_diseases(image_bytes)
        inat_response = call_inat(image_bytes)

        raw_results = dp_response.get("results", [])
        diseases_and_pests = format_disease_pest_list(raw_results)

        return {
            "status": "success",
            "mode": req.caseType,
            "plantnet": dp_response,
            "inat": inat_response,
            "diseases_and_pests_top": diseases_and_pests
        }

    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }


@app.get("/")
def root():
    return {
        "status": "ok",
        "version": "inat-integrated-v1"
    }
