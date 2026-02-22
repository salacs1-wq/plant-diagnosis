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
MAX_IMAGES_PER_CASE = int(os.getenv("MAX_IMAGES_PER_CASE", "5"))

DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;]+);base64,(?P<data>.+)$", re.IGNORECASE | re.DOTALL)

# ----------------------------
# Lifespan (reuse HTTP client)
# ----------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    timeout = httpx.Timeout(DEFAULT_TIMEOUT, connect=DEFAULT_CONNECT_TIMEOUT)
    app.state.http = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
    try:
        yield
    finally:
        await app.state.http.aclose()

app = FastAPI(
    title="Növénydiagnosztikai API",
    version="1.1.5",
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
def _client() -> httpx.AsyncClient:
    return app.state.http

def _require_api_key() -> None:
    if not PLANTNET_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Hiányzik a PLANTNET_API_KEY környezeti változó (Render -> Environment).",
        )

def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return resp.text

def _parse_organs(organs: Optional[str]) -> List[str]:
    """
    User input -> list. Empty list means: do NOT send organs to PlantNet.
    """
    if organs is None:
        return []
    organs = organs.strip()
    if not organs:
        return []
    parts = re.split(r"[,\s]+", organs)
    parts = [p for p in parts if p]
    return parts

def _plantnet_params(project: Optional[str] = None, organs_list: Optional[List[str]] = None) -> List[Tuple[str, str]]:
    """
    IMPORTANT: default: do NOT send organs.
    PlantNet accepts repeated organs params when provided:
      ...?api-key=XXX&organs=leaf&organs=flower
    """
    params: List[Tuple[str, str]] = [("api-key", PLANTNET_API_KEY)]
    if project is not None:
        # diseases endpoint uses 'project' query param; identify uses project in path, so keep optional.
        params.append(("project", str(project)))

    if organs_list:
        for o in organs_list:
            params.append(("organs", o))

    return params

def _decode_base64_image(image_base64: str) -> Tuple[bytes, str]:
    """
    Accepts:
      - raw base64
      - data URL: data:image/jpeg;base64,...
    Fixes padding.
    Returns: (bytes, mime)
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

async def _download_openai_file(url: str) -> bytes:
    """
    Downloads the OpenAI-provided file URL (signed). Async only.
    """
    if not url:
        raise HTTPException(status_code=422, detail="Hiányzik: download_link")

    r = await _client().get(url)
    if r.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"stage": "download", "status": r.status_code, "error": _safe_json(r)},
        )
    data = r.content
    if not data or len(data) < 50:
        raise HTTPException(status_code=422, detail="A letöltött kép üres vagy túl kicsi.")
    return data

def _guess_mime(filename: Optional[str], mime_type: Optional[str]) -> str:
    if mime_type:
        return mime_type
    if not filename:
        return "image/jpeg"
    fn = filename.lower()
    if fn.endswith(".png"):
        return "image/png"
    if fn.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"

# ----------------------------
# Endpoints
# ----------------------------
@app.get("/")
async def rootGet():
    return {"status": "ok"}

@app.get("/health")
async def healthGet():
    return {"status": "ok"}

@app.get("/version")
async def versionGet():
    import sys
    return {
        "version": app.version,
        "python": sys.version,
        "httpx": getattr(httpx, "__version__", "unknown"),
    }

@app.post("/identify")
async def identifyPlant(
    image: UploadFile = File(...),
    # IMPORTANT: default None => we do NOT forward organs to PlantNet by default
    organs: Optional[str] = Query(default=None, description="Opcionális: leaf/flower/fruit/bark. Ha üres, nem küldjük a PlantNet felé."),
    project: str = Query(default=PLANTNET_PROJECT, description="PlantNet project, pl. weurope vagy all"),
):
    _require_api_key()

    img_bytes = await image.read()
    if not img_bytes or len(img_bytes) < 50:
        raise HTTPException(status_code=422, detail="A feltöltött kép üres vagy túl kicsi.")

    organs_list = _parse_organs(organs)

    url = f"{PLANTNET_BASE_URL}/v2/identify/{project}"
    params = _plantnet_params(organs_list=organs_list)
    files = {
        "images": (image.filename or "image.jpg", img_bytes, image.content_type or "image/jpeg")
    }

    r = await _client().post(url, params=params, files=files)

    if r.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"plantnet_stage": "identify", "plantnet_status": r.status_code, "plantnet_error": _safe_json(r)},
        )

    out = _compact_species_response(r.json())
    out["meta"]["organs_sent"] = organs_list or None
    return out

@app.post("/identify_b64")
async def identifyPlantB64(
    payload: Dict[str, Any] = Body(
        ...,
        example={"image_base64": "data:image/jpeg;base64,...", "organs": None, "project": "weurope"},
    )
):
    _require_api_key()

    image_base64 = (payload.get("image_base64") or "").strip()
    if not image_base64:
        raise HTTPException(status_code=422, detail="Hiányzik: image_base64")

    project = str(payload.get("project") or PLANTNET_PROJECT)
    organs_list = _parse_organs(payload.get("organs"))

    img_bytes, mime = _decode_base64_image(image_base64)

    url = f"{PLANTNET_BASE_URL}/v2/identify/{project}"
    params = _plantnet_params(organs_list=organs_list)
    files = {"images": ("image.jpg", img_bytes, mime)}

    r = await _client().post(url, params=params, files=files)

    if r.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"plantnet_stage": "identify", "plantnet_status": r.status_code, "plantnet_error": _safe_json(r)},
        )

    out = _compact_species_response(r.json())
    out["meta"]["organs_sent"] = organs_list or None
    return out

@app.post("/diseases/identify")
async def identifyDisease(
    image: UploadFile = File(...),
    # IMPORTANT: PlantNet diseases endpoint rejects unknown query keys. Keep only what it expects.
    # project is optional; if PlantNet rejects it in your plan, set project=None in calls.
    project: Optional[str] = Query(default=PLANTNET_PROJECT, description="Opcionális. Ha a PlantNet elutasítja, hagyd üresen."),
):
    _require_api_key()

    img_bytes = await image.read()
    if not img_bytes or len(img_bytes) < 50:
        raise HTTPException(status_code=422, detail="A feltöltött kép üres vagy túl kicsi.")

    url = f"{PLANTNET_BASE_URL}/v2/diseases/identify"
    params = [("api-key", PLANTNET_API_KEY)]
    if project:
        params.append(("project", str(project)))

    files = {"images": (image.filename or "image.jpg", img_bytes, image.content_type or "image/jpeg")}

    r = await _client().post(url, params=params, files=files)

    if r.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"plantnet_stage": "diseases", "plantnet_status": r.status_code, "plantnet_error": _safe_json(r)},
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

    project = payload.get("project")  # may be None
    img_bytes, mime = _decode_base64_image(image_base64)

    url = f"{PLANTNET_BASE_URL}/v2/diseases/identify"
    params = [("api-key", PLANTNET_API_KEY)]
    if project:
        params.append(("project", str(project)))

    files = {"images": ("image.jpg", img_bytes, mime)}

    r = await _client().post(url, params=params, files=files)

    if r.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"plantnet_stage": "diseases", "plantnet_status": r.status_code, "plantnet_error": _safe_json(r)},
        )

    return _compact_disease_response(r.json())

@app.post("/diagnose")
async def diagnose(
    image: UploadFile = File(...),
    # IMPORTANT: default None => do NOT forward organs to PlantNet by default
    organs: Optional[str] = Query(default=None, description="Opcionális. Ha üres, nem küldjük a PlantNet felé."),
    project: str = Query(default=PLANTNET_PROJECT, description="PlantNet project, pl. weurope vagy all"),
):
    """
    Kombinált diagnózis:
      1) /v2/identify/{project}
      2) /v2/diseases/identify
    """
    _require_api_key()

    img_bytes = await image.read()
    if not img_bytes or len(img_bytes) < 50:
        raise HTTPException(status_code=422, detail="A feltöltött kép üres vagy túl kicsi.")

    organs_list = _parse_organs(organs)

    identify_url = f"{PLANTNET_BASE_URL}/v2/identify/{project}"
    identify_params = _plantnet_params(organs_list=organs_list)
    identify_files = {"images": (image.filename or "image.jpg", img_bytes, image.content_type or "image/jpeg")}

    disease_url = f"{PLANTNET_BASE_URL}/v2/diseases/identify"
    # NOTE: some PlantNet setups reject project here; keep it optional by default
    disease_params = [("api-key", PLANTNET_API_KEY)]
    disease_files = {"images": (image.filename or "image.jpg", img_bytes, image.content_type or "image/jpeg")}

    r1 = await _client().post(identify_url, params=identify_params, files=identify_files)
    if r1.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"plantnet_stage": "identify", "plantnet_status": r1.status_code, "plantnet_error": _safe_json(r1)},
        )

    r2 = await _client().post(disease_url, params=disease_params, files=disease_files)
    if r2.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"plantnet_stage": "diseases", "plantnet_status": r2.status_code, "plantnet_error": _safe_json(r2)},
        )

    plant_compact = _compact_species_response(r1.json())
    plant_compact["meta"]["organs_sent"] = organs_list or None
    disease_compact = _compact_disease_response(r2.json())

    return {
        "plant": plant_compact,
        "diseaseOrPest": disease_compact,
        "summary": {
            "bestPlant": plant_compact.get("bestMatch"),
            "plantScore": (plant_compact.get("confidence") or {}).get("top1_score"),
            "topPlants": plant_compact.get("topMatches") or [],
            "bestIssue": disease_compact.get("bestMatch"),
            "issueScore": (disease_compact.get("confidence") or {}).get("top1_score"),
            "topIssues": disease_compact.get("topMatches") or [],
            "project": project,
            "organsSent": organs_list or None,
        },
    }

@app.post("/diagnose_files")
async def diagnose_files(
    payload: Dict[str, Any] = Body(
        ...,
        example={
            "openaiFileIdRefs": [
                {"name": "img1.jpg", "mime_type": "image/jpeg", "download_link": "https://..."}
            ],
            "project": "weurope",
            "organs": None,
            "mode": "learning",
            "caseType": "weed",
        },
    )
):
    """
    GPT Actions-friendly endpoint.
    Expects OpenAI file refs (signed download_link), downloads 1..5 images, then calls PlantNet:
      - identify/{project} (species)
      - diseases/identify (disease/pest candidates)
    IMPORTANT: default organs: NOT sent to PlantNet.
    """
    _require_api_key()

    refs = payload.get("openaiFileIdRefs") or payload.get("openai_file_id_refs") or []
    if not isinstance(refs, list) or len(refs) == 0:
        raise HTTPException(status_code=422, detail="Hiányzik vagy üres: openaiFileIdRefs (1–5 elem).")

    if len(refs) > MAX_IMAGES_PER_CASE:
        refs = refs[:MAX_IMAGES_PER_CASE]

    project = str(payload.get("project") or PLANTNET_PROJECT)

    # IMPORTANT: default None => do NOT forward
    organs_list = _parse_organs(payload.get("organs"))

    mode = str(payload.get("mode") or "learning")
    case_type = str(payload.get("caseType") or "weed")

    # 1) download images
    images: List[Tuple[str, bytes, str]] = []
    for i, ref in enumerate(refs, start=1):
        if not isinstance(ref, dict):
            continue
        name = ref.get("name") or f"image_{i}.jpg"
        mime = _guess_mime(name, ref.get("mime_type"))
        url = ref.get("download_link") or ref.get("downloadLink") or ref.get("url")
        data = await _download_openai_file(str(url or ""))
        images.append((name, data, mime))

    if not images:
        raise HTTPException(status_code=422, detail="Nem sikerült képet letölteni az openaiFileIdRefs alapján.")

    # Prepare multipart for PlantNet: images repeated key.
    # httpx supports: files=[("images", (filename, content, mime)), ...]
    identify_files: List[Tuple[str, Tuple[str, bytes, str]]] = []
    disease_files: List[Tuple[str, Tuple[str, bytes, str]]] = []
    for (name, data, mime) in images:
        identify_files.append(("images", (name, data, mime)))
        disease_files.append(("images", (name, data, mime)))

    # 2) PlantNet identify
    identify_url = f"{PLANTNET_BASE_URL}/v2/identify/{project}"
    identify_params = _plantnet_params(organs_list=organs_list)

    r1 = await _client().post(identify_url, params=identify_params, files=identify_files)
    if r1.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"plantnet_stage": "identify", "plantnet_status": r1.status_code, "plantnet_error": _safe_json(r1)},
        )

    # 3) PlantNet diseases (do NOT send project by default; many plans reject it)
    disease_url = f"{PLANTNET_BASE_URL}/v2/diseases/identify"
    disease_params = [("api-key", PLANTNET_API_KEY)]

    r2 = await _client().post(disease_url, params=disease_params, files=disease_files)
    if r2.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"plantnet_stage": "diseases", "plantnet_status": r2.status_code, "plantnet_error": _safe_json(r2)},
        )

    plant_compact = _compact_species_response(r1.json())
    plant_compact["meta"]["organs_sent"] = organs_list or None
    disease_compact = _compact_disease_response(r2.json())

    # A GPT-nek mindig legyen Top3 látható (ez csökkenti az "app vs GPT" bizalomhibát)
    summary = {
        "bestPlant": plant_compact.get("bestMatch"),
        "plantScore": (plant_compact.get("confidence") or {}).get("top1_score"),
        "topPlants": plant_compact.get("topMatches") or [],
        "bestIssue": disease_compact.get("bestMatch"),
        "issueScore": (disease_compact.get("confidence") or {}).get("top1_score"),
        "topIssues": disease_compact.get("topMatches") or [],
        "project": project,
        "organsSent": organs_list or None,
        "mode": mode,
        "caseType": case_type,
        "imageCount": len(images),
    }

    return {
        "plant": plant_compact,
        "diseaseOrPest": disease_compact,
        "summary": summary,
    }
