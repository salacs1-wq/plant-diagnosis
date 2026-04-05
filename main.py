from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional, Any, Dict
import requests
import os

app = FastAPI()

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY")


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


def validate_request(req: DiagnoseRequest) -> None:
    if req.project != "k-middle-europe":
        raise ValueError("A project mezőnek 'k-middle-europe' értékűnek kell lennie.")

    if req.mode != "expert":
        raise ValueError("A mode mezőnek 'expert' értékűnek kell lennie.")

    if req.caseType not in ["weed", "disease", "pest"]:
        raise ValueError("A caseType csak 'weed', 'disease' vagy 'pest' lehet.")


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


def safe_post(url: str, params: Dict[str, Any], files: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    response = requests.post(
        url,
        params=params,
        files=files,
        data=data,
        timeout=60
    )

    print("=== PLANTNET RESPONSE ===")
    print("status_code:", response.status_code)
    print("text:", response.text[:1000])
    print("=========================")

    if not response.ok:
        raise ValueError(f"PlantNet hiba {response.status_code}: {response.text}")

    return response.json()


def call_plantnet_identify(image_bytes: bytes, project: str) -> Dict[str, Any]:
    if not PLANTNET_API_KEY:
        raise ValueError("Hiányzik a PLANTNET_API_KEY környezeti változó.")

    url = f"https://my-api.plantnet.org/v2/identify/{project}"
    params = {"api-key": PLANTNET_API_KEY}
    files = {"images": ("image.jpg", image_bytes, "image/jpeg")}
    data = {"organs": "leaf"}

    print("=== PLANTNET DEBUG /diagnose ===")
    print("url:", url)
    print("params:", params)
    print("data:", data)
    print("================================")

    return safe_post(url, params, files, data)


def call_plantnet_diseases(image_bytes: bytes) -> Dict[str, Any]:
    if not PLANTNET_API_KEY:
        raise ValueError("Hiányzik a PLANTNET_API_KEY környezeti változó.")

    url = "https://my-api.plantnet.org/v2/diseases/identify"
    params = {"api-key": PLANTNET_API_KEY}
    files = {"images": ("image.jpg", image_bytes, "image/jpeg")}
    data = {"organs": "leaf"}

    print("=== PLANTNET DEBUG /diagnose-dp ===")
    print("url:", url)
    print("params:", params)
    print("data:", data)
    print("===================================")

    return safe_post(url, params, files, data)


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
        species = item.get("species", {}) if isinstance(item.get("species"), dict) else {}
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
            "raw_type": first_nonempty_str([
                item.get("type"),
                item.get("category"),
                item.get("entityType"),
                item.get("kind"),
                item.get("healthIssueType")
            ])
        })

    return formatted


def extract_crop_candidates(dp_response: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ["cropCandidates", "crops", "crop_matches", "cropMatches"]:
        value = dp_response.get(key)
        if isinstance(value, list) and value:
            return format_crop_top5(value)

    return []


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

        plantnet_response = call_plantnet_identify(image_bytes, req.project)

        raw_results = plantnet_response.get("results", [])
        if not isinstance(raw_results, list):
            raw_results = []

        top5 = format_weed_top5(raw_results)

        return {
            "status": "success",
            "mode": "weed",
            "process": {
                "endpoint_used": "/diagnose",
                "plantnet_called": True,
                "project_used": req.project
            },
            "top_match": top5[0] if top5 else None,
            "plantnet_top5": top5,
            "raw_count": len(raw_results),
            "raw_response": plantnet_response
        }

    except Exception as e:
        return {
            "status": "error",
            "mode": req.caseType if hasattr(req, "caseType") else None,
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

        dp_response = call_plantnet_diseases(image_bytes)

        raw_results = dp_response.get("results", [])
        if not isinstance(raw_results, list):
            raw_results = []

        crop_top5 = extract_crop_candidates(dp_response)
        diseases_and_pests_top = format_disease_pest_list(raw_results)

        return {
            "status": "success",
            "mode": req.caseType,
            "process": {
                "endpoint_used": "/diagnose-dp",
                "plantnet_called": True,
                "project_used": req.project
            },
            "crop_top5": crop_top5,
            "plantnet_top5": diseases_and_pests_top[:5],
            "raw_count": len(raw_results),
            "raw_response": dp_response
        }

    except Exception as e:
        return {
            "status": "error",
            "mode": req.caseType if hasattr(req, "caseType") else None,
            "message": str(e)
        }


@app.get("/debug-env")
def debug_env():
    plantnet_key = os.getenv("PLANTNET_API_KEY")
    return {
        "plantnet_key_present": bool(plantnet_key),
        "plantnet_key_length": len(plantnet_key) if plantnet_key else 0
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def root():
    return {
        "status": "ok",
        "version": "plantnet-only-v4-safe",
        "endpoints": ["/diagnose", "/diagnose-dp", "/debug-env", "/health"]
    }