from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional, Any, Dict
import requests
import os
from openai import OpenAI

app = FastAPI()

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


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
# FILE
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
# HELPERS
# =========================
def safe_json(response: requests.Response) -> Dict[str, Any]:
    try:
        return response.json()
    except Exception:
        return {
            "raw_text": response.text,
            "status_code": response.status_code
        }


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


def pick_hungarian_name(species: Dict[str, Any]) -> Optional[str]:
    common_names = species.get("commonNames", [])
    if common_names and isinstance(common_names, list):
        return common_names[0]
    return None


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
# PLANTNET
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
    result = safe_json(response)
    result["debug_plantnet_called"] = True
    return result


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
    result = safe_json(response)
    result["debug_plantnet_disease_called"] = True
    return result


# =========================
# INAT
# =========================
def call_inat(image_bytes: bytes) -> Dict[str, Any]:
    url = "https://api.inaturalist.org/v1/computervision/score_image"

    plant_response = requests.post(
        url,
        files={"image": ("image.jpg", image_bytes, "image/jpeg")},
        data={"taxon_id": 47126},  # Plantae
        timeout=60
    )
    plant_response.raise_for_status()
    plant = safe_json(plant_response)

    insect_response = requests.post(
        url,
        files={"image": ("image.jpg", image_bytes, "image/jpeg")},
        data={"taxon_id": 47158},  # Insecta
        timeout=60
    )
    insect_response.raise_for_status()
    insect = safe_json(insect_response)

    return {
        "plant": plant.get("results", [])[:3] if isinstance(plant, dict) else [],
        "insect": insect.get("results", [])[:3] if isinstance(insect, dict) else [],
        "debug_inat_called": True
    }


# =========================
# GPT
# =========================
SYSTEM_PROMPT = """
Te egy növényorvosi diagnosztikai rendszer vagy.

Három módban dolgozol:
- weed = gyom
- disease = betegség
- pest = kártevő

A bemenet tartalmazhat:
- képet közvetetten, backend eredményeken át
- PlantNet eredményt
- iNaturalist eredményt

Általános szabályok:
- Nem találhatsz ki adatot.
- A backend eredmény elsőbbséget élvez.
- Ha bizonytalan vagy, jelezd.
- Rövid, szakmai, strukturált JSON választ adj.

Kártevő módnál Dr. Keszthelyi-féle logikát használd:
1. szerv
2. mechanizmus
3. kárkép
4. kártevő kategória
5. bizonyosság

JSON formátum:
{
  "szerv": "",
  "mechanizmus": "",
  "kartep": "",
  "karosito_kategoria": "",
  "karosito_pontositas": "",
  "bizonyossag": "",
  "indoklas": ""
}
""".strip()


def call_gpt(payload: Dict[str, Any]) -> Dict[str, Any]:
    if client is None:
        return {
            "debug_gpt_called": False,
            "error": "Hiányzik az OPENAI_API_KEY környezeti változó."
        }

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": str(payload)}
        ],
        temperature=0
    )

    return {
        "debug_gpt_called": True,
        "content": response.choices[0].message.content
    }


# =========================
# ROUTES
# =========================
@app.post("/diagnose")
async def diagnose_weed(req: DiagnoseRequest):
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

        plantnet = call_plantnet_identify(image_bytes)
        try:
            inat = call_inat(image_bytes)
        except Exception as e:
            inat = {"error": str(e)}

        results = plantnet.get("results", [])
        if not isinstance(results, list):
            results = []

        weed_top5 = format_weed_top5(results)

        gpt_input = {
            "mode": req.caseType,
            "plantnet_top5": weed_top5,
            "inat": inat
        }

        if plantnet and "results" in plantnet:
            gpt_result = call_gpt(gpt_input)
        else:
            gpt_result = {
            "error": "No PlantNet result",
            "debug": True
            }

        return {
            "status": "success",
            "mode": "weed",
            "debug": {
                "endpoint": "/diagnose",
                "plantnet_called": plantnet.get("debug_plantnet_called", False),
                "inat_called": inat.get("debug_inat_called", False),
                "gpt_called": gpt_result.get("debug_gpt_called", False)
            },
            "plantnet": {
                "debug_plantnet_called": plantnet.get("debug_plantnet_called", False),
                "top5": weed_top5
            },
            "inat": inat,
            "gpt": gpt_result
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

        plantnet = call_plantnet_diseases(image_bytes)
        inat = call_inat(image_bytes)

        raw_results = plantnet.get("results", [])
        if not isinstance(raw_results, list):
            raw_results = []

        diseases_and_pests_top = format_disease_pest_list(raw_results)

        gpt_input = {
            "mode": req.caseType,
            "plantnet_top": diseases_and_pests_top[:5],
            "inat": inat
        }

        gpt_result = call_gpt(gpt_input)

        return {
            "status": "success",
            "mode": req.caseType,
            "debug": {
                "endpoint": "/diagnose-dp",
                "plantnet_called": plantnet.get("debug_plantnet_disease_called", False),
                "inat_called": inat.get("debug_inat_called", False),
                "gpt_called": gpt_result.get("debug_gpt_called", False)
            },
            "plantnet": {
                "debug_plantnet_disease_called": plantnet.get("debug_plantnet_disease_called", False),
                "top": diseases_and_pests_top
            },
            "inat": inat,
            "gpt": gpt_result
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
        "version": "inat-debug-v1",
        "inat_enabled": True,
        "gpt_enabled": True,
        "plantnet_enabled": True
    }
@app.get("/debug-env")
def debug_env():
    import os

    plantnet_key = os.getenv("PLANTNET_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    return {
        "plantnet_key_present": bool(plantnet_key),
        "plantnet_key_length": len(plantnet_key) if plantnet_key else 0,
        "openai_key_present": bool(openai_key),
        "openai_key_length": len(openai_key) if openai_key else 0,
        "openai_key_starts_with_sk": openai_key.startswith("sk-") if openai_key else False
    }
