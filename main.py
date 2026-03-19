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
# COMMON FORMATTERS
# =========================
def pick_hungarian_name(species: Dict[str, Any]) -> Optional[str]:
    common_names = species.get("commonNames", [])
    if common_names and isinstance(common_names, list):
        return common_names[0]
    return None


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


# =========================
# DISEASE / PEST HELPERS
# =========================
PEST_KEYWORDS = [
    "pest", "insect", "mite", "aphid", "thrips", "beetle", "weevil", "leaf beetle",
    "fly", "moth", "bug", "hopper", "cicad", "caterpillar", "larva", "slug", "snail",
    "atka", "levéltetű", "tripsz", "bogár", "ormányos", "légy", "moly", "poloska",
    "kabóca", "hernyó", "lárva", "csiga", "meztelencsiga", "oulema"
]

DISEASE_KEYWORDS = [
    "disease", "fungus", "fungal", "mildew", "rust", "blight", "spot", "mold",
    "rot", "smut", "septoria", "fusarium", "powdery mildew", "downy mildew",
    "peronospora", "blumeria", "alternaria", "necrosis", "virus", "bacteria",
    "betegség", "gomba", "lisztharmat", "rozsda", "foltosság", "penész",
    "rothadás", "üszög", "fuzárium", "peronoszpóra", "alternária"
]


def stringify_item(item: Dict[str, Any]) -> str:
    return " ".join([
        str(item.get("label", "")),
        str(item.get("name", "")),
        str(item.get("title", "")),
        str(item.get("common_name", "")),
        str(item.get("commonName", "")),
        str(item.get("scientificName", "")),
        str(item.get("scientificNameWithoutAuthor", "")),
        str(item.get("type", "")),
        str(item.get("category", "")),
        str(item.get("entityType", "")),
        str(item.get("kind", "")),
        str(item.get("healthIssueType", "")),
    ]).lower()


def classify_dp_result(item: Dict[str, Any]) -> str:
    text = stringify_item(item)

    if any(keyword in text for keyword in PEST_KEYWORDS):
        return "pest"

    if any(keyword in text for keyword in DISEASE_KEYWORDS):
        return "disease"

    # explicit fields if present
    for key in ["type", "category", "entityType", "kind", "healthIssueType"]:
        val = item.get(key)
        if isinstance(val, str):
            low = val.lower()
            if "pest" in low or "insect" in low:
                return "pest"
            if "disease" in low or "fung" in low:
                return "disease"

    return "unknown"


def format_dp_item(item: Dict[str, Any], rank: int) -> Dict[str, Any]:
    species = item.get("species", {}) if isinstance(item.get("species"), dict) else {}
    return {
        "rank": rank,
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
        "score": normalize_score(item)
    }


def extract_crop_candidates(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates = []

    # 1) direct cropCandidates / crops
    for key in ["cropCandidates", "crops", "crop_matches", "cropMatches"]:
        value = data.get(key)
        if isinstance(value, list):
            for idx, item in enumerate(value[:5], start=1):
                species = item.get("species", {}) if isinstance(item, dict) else {}
                candidates.append({
                    "rank": idx,
                    "latin_name": first_nonempty_str([
                        item.get("scientificName") if isinstance(item, dict) else None,
                        item.get("scientificNameWithoutAuthor") if isinstance(item, dict) else None,
                        species.get("scientificNameWithoutAuthor"),
                        item.get("name") if isinstance(item, dict) else None,
                        item.get("label") if isinstance(item, dict) else None,
                    ]),
                    "hungarian_name": first_nonempty_str([
                        item.get("common_name") if isinstance(item, dict) else None,
                        item.get("commonName") if isinstance(item, dict) else None,
                        pick_hungarian_name(species)
                    ]),
                    "score": normalize_score(item if isinstance(item, dict) else {})
                })
            if candidates:
                return candidates

    # 2) fallback from top-level plant identification result
    results = data.get("results", [])
    if isinstance(results, list):
        for idx, item in enumerate(results[:5], start=1):
            species = item.get("species", {})
            latin = first_nonempty_str([
                item.get("scientificName"),
                item.get("scientificNameWithoutAuthor"),
                species.get("scientificNameWithoutAuthor")
            ])
            if latin:
                candidates.append({
                    "rank": idx,
                    "latin_name": latin,
                    "hungarian_name": pick_hungarian_name(species),
                    "score": normalize_score(item)
                })

    return candidates[:5]


def split_disease_pest_results(data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    raw_results = data.get("results", [])
    if not isinstance(raw_results, list):
        raw_results = []

    disease_items = []
    pest_items = []

    for item in raw_results:
        if not isinstance(item, dict):
            continue

        category = classify_dp_result(item)

        if category == "disease":
            disease_items.append(item)
        elif category == "pest":
            pest_items.append(item)

    disease_top5 = [
        format_dp_item(item, idx)
        for idx, item in enumerate(disease_items[:5], start=1)
    ]

    pest_top5 = [
        format_dp_item(item, idx)
        for idx, item in enumerate(pest_items[:5], start=1)
    ]

    return {
        "disease_top5": disease_top5,
        "pest_top5": pest_top5
    }


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

        plantnet_response = call_plantnet_identify(image_bytes)
        results = plantnet_response.get("results", [])
        top5 = format_weed_top5(results)

        return {
            "status": "success",
            "mode": "weed",
            "message": "Sikeres gyomdiagnózis",
            "top_match": top5[0] if top5 else None,
            "top5": top5,
            "raw_count": len(results)
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
        split_results = split_disease_pest_results(dp_response)
        crop_top5 = extract_crop_candidates(dp_response)

        return {
            "status": "success",
            "mode": req.caseType,
            "message": "Sikeres betegség/kártevő diagnózis",
            "disease_top5": split_results["disease_top5"],
            "pest_top5": split_results["pest_top5"],
            "crop_top5": crop_top5,
            "raw_count": len(dp_response.get("results", [])) if isinstance(dp_response.get("results", []), list) else 0
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
        "version": "stable-gpt-2-endpoints-v1"
    }
