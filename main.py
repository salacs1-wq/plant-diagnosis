import os
import json
from typing import Any, Dict, List, Optional, Tuple, Literal

import anyio
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# ============================================================
# CONFIG
# ============================================================

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_BASE_URL = os.getenv("PLANTNET_BASE_URL", "https://my-api.plantnet.org").rstrip("/")

DEFAULT_PROJECT = os.getenv("PLANTNET_DEFAULT_PROJECT", "k-middle-europe")  # <-- fontos
DEFAULT_MODE: Literal["learning", "expert"] = "learning"
DEFAULT_CASETYPE: Literal["weed", "disease", "pest"] = "weed"

TOP_K = 5  # <-- mindig top5-öt adunk vissza


def _require_api_key() -> None:
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="Hiányzik: PLANTNET_API_KEY")


def _httpx_timeout() -> httpx.Timeout:
    return httpx.Timeout(connect=20.0, read=60.0, write=60.0, pool=20.0)


def _safe_json(r: httpx.Response) -> Any:
    try:
        return r.json()
    except Exception:
        return {"text": r.text[:800]}


# ============================================================
# MODELS (request)
# ============================================================

class OpenAIFileRef(BaseModel):
    name: Optional[str] = None
    id: str
    mime_type: Optional[str] = None
    download_link: str


class DiagnoseFilesRequest(BaseModel):
    openaiFileIdRefs: List[OpenAIFileRef] = Field(..., min_length=1, max_length=5)

    # FONTOS: organs alapból ne legyen küldve.
    # Ha a GPT mégis küldi, akkor is OPTIONAL.
    organs: Optional[str] = Field(default=None, description="Opcionális. Ha üres, nem küldjük PlantNet felé.")
    project: Optional[str] = Field(default=None, description="Pl. k-middle-europe | all")
    mode: Optional[Literal["learning", "expert"]] = Field(default=None)
    caseType: Optional[Literal["weed", "disease", "pest"]] = Field(default=None)


# ============================================================
# HELPERS
# ============================================================

def _normalize_project(project: Optional[str]) -> str:
    # Webes fiókban nálad: k-middle-europe
    if not project:
        return DEFAULT_PROJECT
    return project


def _normalize_mode(mode: Optional[str]) -> Literal["learning", "expert"]:
    if mode in ("learning", "expert"):
        return mode  # type: ignore
    return DEFAULT_MODE


def _normalize_case_type(case_type: Optional[str]) -> Literal["weed", "disease", "pest"]:
    if case_type in ("weed", "disease", "pest"):
        return case_type  # type: ignore
    return DEFAULT_CASETYPE


async def _download_bytes(url: str) -> bytes:
    if not url or not isinstance(url, str):
        raise HTTPException(status_code=422, detail="Hiányzik: download_link")

    def _sync_get(u: str) -> httpx.Response:
        with httpx.Client(timeout=_httpx_timeout(), follow_redirects=True) as c:
            return c.get(u)

    r = await anyio.to_thread.run_sync(_sync_get, url)

    if r.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"stage": "download", "status": r.status_code, "text": r.text[:500]},
        )

    data = r.content
    if not data or len(data) < 50:
        raise HTTPException(status_code=422, detail="A letöltött kép üres vagy túl kicsi.")
    return data


def _topk_results_from_plantnet_identify(raw: Dict[str, Any], k: int) -> List[Dict[str, Any]]:
    # PlantNet identify tipikusan: raw["results"] listában adja
    results = raw.get("results") or []
    out = []
    for item in results[:k]:
        sp = item.get("species") or {}
        sci = sp.get("scientificNameWithoutAuthor") or sp.get("scientificName") or "Unknown"
        score = item.get("score", 0.0) or 0.0
        out.append({"name": sci, "score": float(score)})
    return out


def _best_match_from_top(top: List[Dict[str, Any]]) -> str:
    return top[0]["name"] if top else ""


def _best_score_from_top(top: List[Dict[str, Any]]) -> float:
    return float(top[0]["score"]) if top else 0.0


# ============================================================
# PLANTNET CALLS (sync in thread)
# ============================================================

def _sync_post(url: str, params: Dict[str, Any], data, files) -> httpx.Response:
    with httpx.Client(timeout=_httpx_timeout(), follow_redirects=True) as c:
        return c.post(url, params=params, data=data, files=files)


async def _plantnet_identify(
    images: List[Tuple[str, bytes, str]],
    *,
    project: str,
    organs: Optional[str],
) -> Dict[str, Any]:
    """
    PlantNet: /v2/identify/{project}
    - api-key query param
    - images = multipart
    - organs = multipart field (NEM query param)
    """
    _require_api_key()
    if not images:
        raise HTTPException(status_code=422, detail="Nincs kép az azonosításhoz.")

    url = f"{PLANTNET_BASE_URL}/v2/identify/{project}"
    files = [("images", (fn or "image.jpg", b, mt or "image/jpeg")) for (fn, b, mt) in images]

    # FONTOS: organs-t csak akkor küldünk, ha tényleg megadták
    data = None
    if organs:
        # PlantNet több képnél listát várhat; itt egyszerűen ugyanazt adjuk minden képre
        data = [("organs", organs) for _ in images]

    params = {"api-key": PLANTNET_API_KEY}

    r = await anyio.to_thread.run_sync(_sync_post, url, params, data, files)

    if r.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={
                "plantnet_stage": "identify",
                "plantnet_status": r.status_code,
                "plantnet_error": _safe_json(r),
            },
        )
    return r.json()


async def _plantnet_diseases_identify(images: List[Tuple[str, bytes, str]]) -> Dict[str, Any]:
    """
    PlantNet diseases: /v2/diseases/identify
    - api-key query param
    - images multipart
    """
    _require_api_key()
    if not images:
        raise HTTPException(status_code=422, detail="Nincs kép az azonosításhoz.")

    url = f"{PLANTNET_BASE_URL}/v2/diseases/identify"
    params = {"api-key": PLANTNET_API_KEY}
    files = [("images", (fn or "image.jpg", b, mt or "image/jpeg")) for (fn, b, mt) in images]

    r = await anyio.to_thread.run_sync(_sync_post, url, params, None, files)

    if r.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={
                "plantnet_stage": "diseases",
                "plantnet_status": r.status_code,
                "plantnet_error": _safe_json(r),
            },
        )
    return r.json()


def _topk_from_diseases(raw: Dict[str, Any], k: int) -> List[Dict[str, Any]]:
    # PlantNet diseases tipikusan: raw["results"] listában adja
    results = raw.get("results") or []
    out = []
    for item in results[:k]:
        # item: { "disease": {"code": "...", "name": "..."} , "score": ... } – ez változhat
        dis = item.get("disease") or {}
        code = dis.get("code") or dis.get("name") or item.get("name") or "Unknown"
        score = item.get("score", 0.0) or 0.0
        out.append({"name": str(code), "score": float(score)})
    return out


# ============================================================
# RESPONSE SHAPE (egységes)
# ============================================================

def _compact_match(best: str, top: List[Dict[str, Any]], level: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "bestMatch": best,
        "confidence": {"top1_score": _best_score_from_top(top), "level": level},
        "topMatches": top,
        "meta": meta,
    }


def _summary(
    *,
    plant_top: List[Dict[str, Any]],
    issue_top: List[Dict[str, Any]],
    project: str,
    organs_sent: Optional[str],
    mode: str,
    case_type: str,
    image_count: int,
) -> Dict[str, Any]:
    return {
        "bestPlant": _best_match_from_top(plant_top),
        "plantScore": _best_score_from_top(plant_top),
        "topPlants": plant_top,
        "bestIssue": _best_match_from_top(issue_top),
        "issueScore": _best_score_from_top(issue_top),
        "topIssues": issue_top,
        "project": project,
        "organsSent": organs_sent,
        "mode": mode,
        "caseType": case_type,
        "imageCount": image_count,
    }


# ============================================================
# APP
# ============================================================

app = FastAPI(title="Plant Diagnosis Bridge", version="1.0.0")


@app.get("/")
async def root() -> Dict[str, Any]:
    return {"status": "ok"}


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok"}


@app.get("/version")
async def version() -> Dict[str, Any]:
    return {"app": "plant-diagnosis-bridge", "version": "1.0.0"}


@app.post("/diagnose_files")
async def diagnose_files(req: DiagnoseFilesRequest) -> Dict[str, Any]:
    project = _normalize_project(req.project)
    mode = _normalize_mode(req.mode)
    case_type = _normalize_case_type(req.caseType)

    # images letöltése
    images: List[Tuple[str, bytes, str]] = []
    for ref in req.openaiFileIdRefs:
        b = await _download_bytes(ref.download_link)
        images.append((ref.name or "image.jpg", b, ref.mime_type or "image/jpeg"))

    # 1) Növény (weeds) – mindig lefuttatjuk, mert a kárkép/kártevő módban is kell tudni, MI A KULTÚRA / MI A NÖVÉNY.
    #    De a visszaadott "domináns" listát a caseType alapján szűrjük.
    plant_raw = await _plantnet_identify(images, project=project, organs=req.organs)
    plant_top = _topk_results_from_plantnet_identify(plant_raw, TOP_K)

    # 2) Betegség/kártevő – PlantNet diseases endpoint (ha kell)
    issue_top: List[Dict[str, Any]] = []
    issue_raw: Optional[Dict[str, Any]] = None
    if case_type in ("disease", "pest"):
        issue_raw = await _plantnet_diseases_identify(images)
        issue_top = _topk_from_diseases(issue_raw, TOP_K)

    plant = _compact_match(
        best=_best_match_from_top(plant_top),
        top=plant_top,
        level="species",
        meta={"project": project, "language": "en", "organs_sent": req.organs},
    )

    disease_or_pest = _compact_match(
        best=_best_match_from_top(issue_top),
        top=issue_top,
        level=("disease_or_pest" if case_type in ("disease", "pest") else "none"),
        meta={"project": project},
    )

    summary = _summary(
        plant_top=plant_top,
        issue_top=issue_top,
        project=project,
        organs_sent=req.organs,
        mode=mode,
        case_type=case_type,
        image_count=len(images),
    )

    # SZŰRÉS: ha weed mód, ne „tolja előre” a diseases listát a GPT felé
    if case_type == "weed":
        disease_or_pest = _compact_match(best="", top=[], level="none", meta={"project": project})
        summary["bestIssue"] = ""
        summary["issueScore"] = 0.0
        summary["topIssues"] = []

    return {"plant": plant, "diseaseOrPest": disease_or_pest, "summary": summary}
