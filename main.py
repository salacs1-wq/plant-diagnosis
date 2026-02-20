import os
import re
import base64
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, File, UploadFile, Query, Body, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

# ----------------------------
# Config
# ----------------------------
PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_PROJECT = os.getenv("PLANTNET_PROJECT", "weurope").strip()
PLANTNET_BASE_URL = os.getenv("PLANTNET_BASE_URL", "https://my-api.plantnet.org").rstrip("/")

DEFAULT_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "30"))
DEFAULT_CONNECT_TIMEOUT = float(os.getenv("HTTP_CONNECT_TIMEOUT", "10"))

DATA_URL_RE = re.compile(
    r"^data:(?P<mime>[^;]+);base64,(?P<data>.+)$",
    re.IGNORECASE | re.DOTALL
)

# ----------------------------
# Lifespan (reuse HTTP client)
# ----------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    timeout = httpx.Timeout(DEFAULT_TIMEOUT, connect=DEFAULT_CONNECT_TIMEOUT)
    app.state.http = httpx.AsyncClient(timeout=timeout)
    try:
        yield
    finally:
        await app.state.http.aclose()

app = FastAPI(
    title="Növénydiagnosztikai API",
    version="1.0.0",
    description="PlantNet proxy (növényazonosítás + betegség/kártevő azonosítás) GPT-hez optimalizált válaszokkal.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------
# Helpers
# ----------------------------
def _require_api_key() -> None:
    if not PLANTNET_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Hiányzik a PLANTNET_API_KEY környezeti változó (Render -> Environment).",
        )

def _normalize_organs(organs: Optional[str]) -> List[str]:
    if not organs:
        return ["leaf"]
    parts = re.split(r"[,\s]+", organs.strip())
    parts = [p for p in parts if p]
    return parts or ["leaf"]

def _decode_base64_image(image_base64: str) -> Tuple[bytes, str]:
    """
    Elfogad:
      - tiszta base64 stringet
      - data URL-t: data:image/jpeg;base64,...
    Javítja a paddingot (====) ha hiányzik.
    Visszaad: (bytes, mime)
    """
    image_base64 = (image_base64 or "").strip()
    if not image_base64:
        raise HTTPException(status_code=422, detail="Hiányzik: image_base64")

    mime = "image/jpeg"
    m = DATA_URL_RE.match(image_base64)
    if m:
        mime = m.group("mime").strip().lower()
        image_base64 = m.group("data").strip()

    image_base64 = re.sub(r"\s+", "", image_base64)

    missing = len(image_base64) % 4
    if missing:
        image_base64 += "=" * (4 - missing)

    try:
        data = base64.b64decode(image_base64, validate=False)
    except Exception:
        raise HTTPException(status_code=422, detail="Nem érvényes base64 (dekódolási hiba).")

    if not data or len(data) < 50:
        raise HTTPException(status_code=422, detail="A kép túl kicsi vagy üres (base64).")

    return data, mime

def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return resp.text

def _compact_species_response(raw: Dict[str, Any]) -> Dict[str, Any]:
    results = raw.get("results") or []
    top_matches: List[Dict[str, Any]] = []
    best = None
    best_score = None

    for r in results[:5]:
        score = r.get("score")
        species = (r.get("species") or {})
        sci = (
            species.get("scientificNameWithoutAuthor")
            or species.get("scientificName")
            or species.get("scientificNameAuthorship")
        )
        if not sci:
            continue
        if best is None:
            best = sci
            best_score = score
        top_matches.append({"name": sci, "score": float(score) if score is not None else None})

    return {
        "bestMatch": best or "ismeretlen",
        "confidence": {
            "top1_score": float(best_score) if best_score is not None else None,
            "level": "species",
        },
        "topMatches": top_matches[:3],
        "meta": {
            "project": (raw.get("query") or {}).get("project") or PLANTNET_PROJECT,
            "language": raw.get("language") or (raw.get("query") or {}).get("language"),
        },
    }

def _compact_disease_response(raw: Dict[str, Any]) -> Dict[str, Any]:
    results = raw.get("results") or raw.get("diseases") or []
    candidates: List[Dict[str, Any]] = []
    top_name = None
    top_score = None

    for r in results[:5]:
        score = r.get("score") or r.get("confidence")
        name = (
            r.get("name")
            or r.get("disease")
            or (r.get("label") if isinstance(r.get("label"), str) else None)
        )

        if not name:
            d = r.get("disease") if isinstance(r.get("disease"), dict) else None
            if d:
                name = d.get("name") or d.get("id")

        if not name:
            continue

        if top_name is None:
            top_name = name
            top_score = score

        candidates.append({"name": name, "score": float(score) if score is not None else None})

    return {
        "bestMatch": top_name or "ismeretlen",
        "confidence": {
            "top1_score": float(top_score) if top_score is not None else None,
            "level": "disease_or_pest",
        },
        "topMatches": candidates[:3],
        "meta": {"project": (raw.get("query") or {}).get("project") or PLANTNET_PROJECT},
    }

def _plantnet_params_with_organs(organs_list: List[str]) -> List[Tuple[str, str]]:
    """
    PlantNet 'organs' mező: többszörös query paramként megbízható:
      ...?api-key=XXX&organs=leaf&organs=flower
    """
    params: List[Tuple[str, str]] = [("api-key", PLANTNET_API_KEY)]
    for o in organs_list:
        params.append(("organs", o))
    return params

def _client(app: FastAPI) -> httpx.AsyncClient:
    return app.state.http

# ----------------------------
# Endpoints
# ----------------------------
@app.get("/")
async def rootGet():
    return {"status": "ok"}

@app.get("/health")
async def healthGet():
    return {"status": "ok"}

@app.post("/identify")
async def identifyPlant(
    image: UploadFile = File(...),
    organs: str = Query("leaf", description="Pl. leaf/flower/fruit/bark (több is lehet: leaf,flower)"),
    project: str = Query(default=PLANTNET_PROJECT, description="PlantNet project, pl. weurope vagy all"),
):
    _require_api_key()

    img_bytes = await image.read()
    if not img_bytes or len(img_bytes) < 50:
        raise HTTPException(status_code=422, detail="A feltöltött kép üres vagy túl kicsi.")

    organs_list = _normalize_organs(organs)

    url = f"{PLANTNET_BASE_URL}/v2/identify/{project}"
    params = _plantnet_params_with_organs(organs_list)
    files = {
        "images": (
            image.filename or "image.jpg",
            img_bytes,
            image.content_type or "image/jpeg",
        )
    }

    r = await _client(app).post(url, params=params, files=files)

    if r.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"plantnet_status": r.status_code, "plantnet_error": _safe_json(r)},
        )

    return _compact_species_response(r.json())

@app.post("/identify_b64")
async def identifyPlantB64(
    payload: Dict[str, Any] = Body(
        ...,
        example={"image_base64": "data:image/jpeg;base64,...", "organs": "leaf", "project": "weurope"},
    )
):
    _require_api_key()

    image_base64 = (payload.get("image_base64") or "").strip()
    if not image_base64:
        raise HTTPException(status_code=422, detail="Hiányzik: image_base64")

    organs = payload.get("organs") or "leaf"
    project = payload.get("project") or PLANTNET_PROJECT

    img_bytes, mime = _decode_base64_image(image_base64)
    organs_list = _normalize_organs(str(organs))

    url = f"{PLANTNET_BASE_URL}/v2/identify/{project}"
    params = _plantnet_params_with_organs(organs_list)
    files = {"images": ("image.jpg", img_bytes, mime)}

    r = await _client(app).post(url, params=params, files=files)

    if r.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"plantnet_status": r.status_code, "plantnet_error": _safe_json(r)},
        )

    return _compact_species_response(r.json())

@app.post("/diseases/identify")
async def identifyDisease(
    image: UploadFile = File(...),
    project: str = Query(default=PLANTNET_PROJECT, description="PlantNet project (ha releváns)"),
):
    _require_api_key()

    img_bytes = await image.read()
    if not img_bytes or len(img_bytes) < 50:
        raise HTTPException(status_code=422, detail="A feltöltött kép üres vagy túl kicsi.")

    url = f"{PLANTNET_BASE_URL}/v2/diseases/identify"
    params = [("api-key", PLANTNET_API_KEY), ("project", str(project))]
    files = {
        "images": (
            image.filename or "image.jpg",
            img_bytes,
            image.content_type or "image/jpeg",
        )
    }

    r = await _client(app).post(url, params=params, files=files)

    if r.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"plantnet_status": r.status_code, "plantnet_error": _safe_json(r)},
        )

    return _compact_disease_response(r.json())

@app.post("/diseases/identify_b64")
async def identifyDiseaseB64(
    payload: Dict[str, Any] = Body(
        ...,
        example={"image_base64": "data:image/jpeg;base64,...", "project": "weurope"},
    )
):
    _require_api_key()

    image_base64 = (payload.get("image_base64") or "").strip()
    if not image_base64:
        raise HTTPException(status_code=422, detail="Hiányzik: image_base64")

    project = payload.get("project") or PLANTNET_PROJECT
    img_bytes, mime = _decode_base64_image(image_base64)

    url = f"{PLANTNET_BASE_URL}/v2/diseases/identify"
    params = [("api-key", PLANTNET_API_KEY), ("project", str(project))]
    files = {"images": ("image.jpg", img_bytes, mime)}

    r = await _client(app).post(url, params=params, files=files)

    if r.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"plantnet_status": r.status_code, "plantnet_error": _safe_json(r)},
        )

    return _compact_disease_response(r.json())

@app.post("/diagnose")
async def diagnose(
    image: UploadFile = File(...),
    organs: str = Query("leaf", description="Pl. leaf/flower/fruit/bark (több is lehet: leaf,flower)"),
    project: str = Query(default=PLANTNET_PROJECT, description="PlantNet project, pl. weurope vagy all"),
):
    """
    Kombinált terepi diagnózis:
      1) /v2/identify/{project}  -> faj/szintű azonosítás (kompakt)
      2) /v2/diseases/identify   -> betegség/kártevő jelöltek (kompakt)
    """
    _require_api_key()

    img_bytes = await image.read()
    if not img_bytes or len(img_bytes) < 50:
        raise HTTPException(status_code=422, detail="A feltöltött kép üres vagy túl kicsi.")

    organs_list = _normalize_organs(organs)

    identify_url = f"{PLANTNET_BASE_URL}/v2/identify/{project}"
    identify_params = _plantnet_params_with_organs(organs_list)
    identify_files = {
        "images": (
            image.filename or "image.jpg",
            img_bytes,
            image.content_type or "image/jpeg",
        )
    }

    disease_url = f"{PLANTNET_BASE_URL}/v2/diseases/identify"
    disease_params = [("api-key", PLANTNET_API_KEY), ("project", str(project))]
    disease_files = {
        "images": (
            image.filename or "image.jpg",
            img_bytes,
            image.content_type or "image/jpeg",
        )
    }

    r1 = await _client(app).post(identify_url, params=identify_params, files=identify_files)
    if r1.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"plantnet_stage": "identify", "plantnet_status": r1.status_code, "plantnet_error": _safe_json(r1)},
        )

    r2 = await _client(app).post(disease_url, params=disease_params, files=disease_files)
    if r2.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"plantnet_stage": "diseases", "plantnet_status": r2.status_code, "plantnet_error": _safe_json(r2)},
        )

    plant_compact = _compact_species_response(r1.json())
    disease_compact = _compact_disease_response(r2.json())

    return {
        "plant": plant_compact,
        "diseaseOrPest": disease_compact,
        "summary": {
            "bestPlant": plant_compact.get("bestMatch"),
            "plantScore": (plant_compact.get("confidence") or {}).get("top1_score"),
            "bestIssue": disease_compact.get("bestMatch"),
            "issueScore": (disease_compact.get("confidence") or {}).get("top1_score"),
            "project": project,
            "organs": organs_list,
        },
    }
