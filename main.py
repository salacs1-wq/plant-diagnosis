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
    # 🔥 fallback értékek
    if req.project != "k-middle-europe":
        req.project = "k-middle-europe"

    if req.mode != "expert":
        req.mode = "expert"

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
            "raw_type": first_nonempty_str([
                item.get("type"),
                item.get("category"),
                item.get("entityType"),
                item.get("kind"),
                item.get("healthIssueType")
            ]),
            "raw_item": item
        })

    return formatted


# =========================
# BUILD HELPERS
# =========================
def build_crop_top5(identify_response: Dict[str, Any]) -> List[Dict[str, Any]]:
    results = identify_response.get("results")

    if not isinstance(results, list) or not results:
        return []

    return format_crop_top5(results)


def build_dp_list(dp_response: Dict[str, Any]) -> List[Dict[str, Any]]:
    results = dp_response.get("results")

    if not isinstance(results, list):
        return []

    return format_disease_pest_list(results)


# =========================
# ROUTES
# =========================
@app.post("/diagnose")
async def diagnose(req: DiagnoseRequest):
    try:
        # 🔥 KŐBE VÉSETT ÉRTÉKEK
        req.project = "k-middle-europe"
        req.mode = "expert"

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

        # ✅ SORTOLÁS (nagyon fontos)
        if isinstance(results, list):
            results = sorted(results, key=lambda x: x.get("score", 0), reverse=True)

        # ✅ HA VAN TALÁLAT
        if results:
    species = results[0].get("species", {})
    latin = species.get("scientificNameWithoutAuthor", "")

    CROP_KEYWORDS = [
        "brassica", "triticum", "zea", "helianthus",
        "glycine", "solanum", "beta", "hordeum"
    ]

    def is_likely_crop(name: str) -> bool:
        if not name:
            return False
        name = name.lower()
        return any(k in name for k in CROP_KEYWORDS)

    # 🔴 HA KULTÚRNÖVÉNY → NE HAZUDJ GYOMOT
    if is_likely_crop(latin):
        return {
            "status": "success",
            "mode": "weed",
            "message": "Valószínűleg kultúrnövény, nem gyom",
            "top_match": None,
            "top5": [],
            "raw_count": len(results),
            "is_crop": True
        }

    # ✅ HA VALÓDI GYOM
   # 🔥 rendezd score szerint
if isinstance(results, list):
    results = sorted(results, key=lambda x: x.get("score", 0), reverse=True)

top5 = format_weed_top5(results)

# 🔥 threshold logika
if top5 and top5[0]["score"] > 20:
    top_match = top5[0]
else:
    top_match = None

# 🔥 fallback ha semmi értelmes nincs
if not top_match:
    return {
        "status": "success",
        "mode": "weed",
        "message": "Nincs megbízható gyomazonosítás",
        "top_match": {
            "rank": 1,
            "latin_name": "Ismeretlen gyom",
            "hungarian_name": None,
            "score": 1.0
        },
        "top5": top5,
        "raw_count": len(results)
    }

# ✅ normál eset
return {
    "status": "success",
    "mode": "weed",
    "message": "Sikeres gyomdiagnózis",
    "top_match": top_match,
    "top5": top5,
    "raw_count": len(results)
}

        # 🔥 FALLBACK (EZ HIÁNYZOTT)
        return {
            "status": "success",
            "mode": "weed",
            "message": "Nincs biztos találat (fallback)",
            "top_match": {
                "rank": 1,
                "latin_name": "Ismeretlen gyom",
                "hungarian_name": None,
                "score": 0.01
            },
            "top5": [],
            "raw_count": 0
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
        # 🔥 KŐBE VÉSETT ÉRTÉKEK
        req.project = "k-middle-europe"
        req.mode = "expert"

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

        crop_top5 = build_crop_top5(identify_response)
        dp_list = build_dp_list(dp_response)

        flags = {
            "identify_ok": not identify_response.get("error", False),
            "dp_ok": not dp_response.get("error", False),
            "has_crop": len(crop_top5) > 0,
            "has_dp": len(dp_list) > 0
        }

        return {
            "status": "success",
            "mode": req.caseType,
            "message": "Stabil betegség/kártevő diagnózis",
            "crop_top5": crop_top5,
            "diseases_and_pests_top": dp_list,
            "context_flags": flags,
            "raw_count": len(dp_list)
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
        "version": "stable-final-v1"
    }
