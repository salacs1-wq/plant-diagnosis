from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional, Any, Dict
import requests
import os
import json
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
# HELPERS
# =========================
def safe_json(response: requests.Response) -> Dict[str, Any]:
    try:
        return response.json()
    except Exception:
        return {
            "error": "Nem JSON válasz érkezett.",
            "status_code": response.status_code,
            "raw_text": response.text[:1000]
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


def flatten_inat_taxon_name(item: Dict[str, Any]) -> str:
    taxon = item.get("taxon", {})
    if isinstance(taxon, dict):
        return first_nonempty_str([
            taxon.get("preferred_common_name"),
            taxon.get("name")
        ]) or "ismeretlen"
    return "ismeretlen"


def flatten_inat_rank(item: Dict[str, Any]) -> Optional[str]:
    taxon = item.get("taxon", {})
    if isinstance(taxon, dict):
        return taxon.get("rank")
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


def format_inat_top5(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    formatted = []

    for idx, item in enumerate(results[:5], start=1):
        formatted.append({
            "rank": idx,
            "name": flatten_inat_taxon_name(item),
            "latin_name": item.get("taxon", {}).get("name") if isinstance(item.get("taxon"), dict) else None,
            "rank_name": flatten_inat_rank(item),
            "score": normalize_score(item)
        })

    return formatted


# =========================
# PLANTNET CALLS
# =========================
def call_plantnet_identify(image_bytes: bytes) -> Dict[str, Any]:
    if not PLANTNET_API_KEY:
        return {
            "ok": False,
            "error": "Hiányzik a PLANTNET_API_KEY környezeti változó."
        }

    url = "https://my-api.plantnet.org/v2/identify/all"
    params = {"api-key": PLANTNET_API_KEY}
    files = {"images": ("image.jpg", image_bytes, "image/jpeg")}
    data = {"organs": "leaf"}

    try:
        response = requests.post(
            url,
            params=params,
            files=files,
            data=data,
            timeout=60
        )
        response.raise_for_status()
        payload = safe_json(response)
        return {
            "ok": True,
            "endpoint": "plantnet_identify",
            "raw": payload,
            "results": payload.get("results", []) if isinstance(payload, dict) else []
        }
    except Exception as e:
        return {
            "ok": False,
            "endpoint": "plantnet_identify",
            "error": str(e)
        }


def call_plantnet_diseases(image_bytes: bytes) -> Dict[str, Any]:
    if not PLANTNET_API_KEY:
        return {
            "ok": False,
            "error": "Hiányzik a PLANTNET_API_KEY környezeti változó."
        }

    url = "https://my-api.plantnet.org/v2/diseases/identify"
    params = {"api-key": PLANTNET_API_KEY}
    files = {"images": ("image.jpg", image_bytes, "image/jpeg")}
    data = {"organs": "leaf"}

    try:
        response = requests.post(
            url,
            params=params,
            files=files,
            data=data,
            timeout=60
        )
        response.raise_for_status()
        payload = safe_json(response)
        return {
            "ok": True,
            "endpoint": "plantnet_diseases",
            "raw": payload,
            "results": payload.get("results", []) if isinstance(payload, dict) else []
        }
    except Exception as e:
        return {
            "ok": False,
            "endpoint": "plantnet_diseases",
            "error": str(e)
        }


# =========================
# INAT CALL
# =========================
def call_inat_one(image_bytes: bytes, taxon_id: int, label: str) -> Dict[str, Any]:
    url = "https://api.inaturalist.org/v1/computervision/score_image"

    try:
        response = requests.post(
            url,
            files={"image": ("image.jpg", image_bytes, "image/jpeg")},
            data={"taxon_id": taxon_id},
            timeout=60
        )
        response.raise_for_status()
        payload = safe_json(response)
        results = payload.get("results", []) if isinstance(payload, dict) else []

        return {
            "ok": True,
            "label": label,
            "raw": payload,
            "top5": format_inat_top5(results)
        }

    except Exception as e:
        return {
            "ok": False,
            "label": label,
            "error": str(e),
            "top5": []
        }


def call_inat(image_bytes: bytes) -> Dict[str, Any]:
    plant = call_inat_one(image_bytes, 47126, "plant")   # Plantae
    insect = call_inat_one(image_bytes, 47158, "insect") # Insecta

    return {
        "ok": plant.get("ok", False) or insect.get("ok", False),
        "plant": plant,
        "insect": insect
    }


# =========================
# GPT
# =========================
SYSTEM_PROMPT = """
Te egy növényorvosi diagnosztikai asszisztens vagy.

Feladat:
- elemezd a backend által visszaadott PlantNet és iNaturalist top találatokat
- fogalmazz meg rövid szakmai elemzést
- adj végső javaslatot

Fontos szabályok:
- ne találj ki olyan tényt, amit a backend nem támaszt alá
- ha az adatok ellentmondanak, ezt jelezd
- ha valamelyik motor hibázott, ezt jelezd
- a válaszod ne JSON legyen, hanem rövid szakmai magyar szöveg

Külön logika:
- weed mód: gyomazonosítás
- disease mód: betegség vagy abiotikus jelleg
- pest mód: használhatod Dr. Keszthelyi-féle kárkép-logikát (szerv, mechanizmus, kárkép), de csak a backend adataival összhangban
""".strip()


def call_gpt(payload: Dict[str, Any]) -> Dict[str, Any]:
    if client is None:
        return {
            "ok": False,
            "error": "Hiányzik az OPENAI_API_KEY környezeti változó."
        }

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
            ],
            temperature=0
        )

        content = response.choices[0].message.content

        return {
            "ok": True,
            "analysis": content
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e)
        }


# =========================
# PIPELINE BUILDERS
# =========================
def build_final_recommendation_weed(plantnet_top5: List[Dict[str, Any]], inat_top5: List[Dict[str, Any]]) -> Dict[str, Any]:
    top_plantnet = plantnet_top5[0] if plantnet_top5 else None
    top_inat = inat_top5[0] if inat_top5 else None

    return {
        "top_plantnet": top_plantnet,
        "top_inat": top_inat,
        "note": "Gyom módban a PlantNet elsődleges, az iNat kiegészítő."
    }


def build_final_recommendation_dp(case_type: str, plantnet_top: List[Dict[str, Any]], inat: Dict[str, Any]) -> Dict[str, Any]:
    top_plantnet = plantnet_top[0] if plantnet_top else None
    top_inat_insect = inat.get("insect", {}).get("top5", [None])[0] if isinstance(inat.get("insect"), dict) else None

    return {
        "mode": case_type,
        "top_plantnet": top_plantnet,
        "top_inat_insect": top_inat_insect,
        "note": "Betegség/kártevő módban a PlantNet és iNat együtt értékelendő."
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
        inat = call_inat(image_bytes)

        plantnet_top5 = format_weed_top5(plantnet.get("results", [])) if plantnet.get("ok") else []
        inat_top5 = inat.get("plant", {}).get("top5", []) if isinstance(inat.get("plant"), dict) else []

        gpt_input = {
            "mode": "weed",
            "plantnet_top5": plantnet_top5,
            "inat_plant_top5": inat_top5,
            "process_state": {
                "plantnet_ok": plantnet.get("ok", False),
                "inat_ok": inat.get("ok", False)
            }
        }

        gpt_result = call_gpt(gpt_input)

        return {
            "status": "success",
            "mode": "weed",
            "process": {
                "download_ok": True,
                "plantnet_ok": plantnet.get("ok", False),
                "inat_ok": inat.get("ok", False),
                "gpt_ok": gpt_result.get("ok", False)
            },
            "plantnet_top5": plantnet_top5,
            "inat_top5": inat_top5,
            "gpt_analysis": gpt_result.get("analysis") if gpt_result.get("ok") else gpt_result.get("error"),
            "final_recommendation": build_final_recommendation_weed(plantnet_top5, inat_top5),
            "debug_raw": {
                "plantnet": plantnet,
                "inat": inat,
                "gpt": gpt_result
            }
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

        plantnet_top = format_disease_pest_list(plantnet.get("results", [])) if plantnet.get("ok") else []

        gpt_input = {
            "mode": req.caseType,
            "plantnet_top5": plantnet_top[:5],
            "inat_plant_top5": inat.get("plant", {}).get("top5", []) if isinstance(inat.get("plant"), dict) else [],
            "inat_insect_top5": inat.get("insect", {}).get("top5", []) if isinstance(inat.get("insect"), dict) else [],
            "process_state": {
                "plantnet_ok": plantnet.get("ok", False),
                "inat_ok": inat.get("ok", False)
            }
        }

        gpt_result = call_gpt(gpt_input)

        return {
            "status": "success",
            "mode": req.caseType,
            "process": {
                "download_ok": True,
                "plantnet_ok": plantnet.get("ok", False),
                "inat_ok": inat.get("ok", False),
                "gpt_ok": gpt_result.get("ok", False)
            },
            "plantnet_top5": plantnet_top[:5],
            "inat_top5": {
                "plant": inat.get("plant", {}).get("top5", []) if isinstance(inat.get("plant"), dict) else [],
                "insect": inat.get("insect", {}).get("top5", []) if isinstance(inat.get("insect"), dict) else []
            },
            "gpt_analysis": gpt_result.get("analysis") if gpt_result.get("ok") else gpt_result.get("error"),
            "final_recommendation": build_final_recommendation_dp(req.caseType, plantnet_top[:5], inat),
            "debug_raw": {
                "plantnet": plantnet,
                "inat": inat,
                "gpt": gpt_result
            }
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
    openai_key = os.getenv("OPENAI_API_KEY")

    return {
        "plantnet_key_present": bool(plantnet_key),
        "plantnet_key_length": len(plantnet_key) if plantnet_key else 0,
        "openai_key_present": bool(openai_key),
        "openai_key_length": len(openai_key) if openai_key else 0,
        "openai_key_starts_with_sk": openai_key.startswith("sk-") if openai_key else False
    }


@app.get("/")
def root():
    return {
        "status": "ok",
        "version": "transparent-v1",
        "endpoints": ["/diagnose", "/diagnose-dp", "/debug-env"]
    }
