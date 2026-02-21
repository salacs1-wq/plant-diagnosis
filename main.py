import os
import re
import traceback
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, Body, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# ----------------------------
# CONFIG
# ----------------------------
PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_PROJECT = os.getenv("PLANTNET_PROJECT", "weurope").strip()
PLANTNET_BASE_URL = os.getenv("PLANTNET_BASE_URL", "https://my-api.plantnet.org").rstrip("/")

HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "30"))
HTTP_CONNECT_TIMEOUT = float(os.getenv("HTTP_CONNECT_TIMEOUT", "10"))

# ----------------------------
# APP
# ----------------------------
app = FastAPI(title="Plant Diagnosis API", version="1.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------
# GLOBAL ERROR HANDLER
# ----------------------------
@app.exception_handler(Exception)
def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "error": "internal_server_error",
                "type": exc.__class__.__name__,
                "message": str(exc),
                "path": request.url.path,
                "trace": traceback.format_exc().splitlines()[-12:],
            }
        },
    )

# ----------------------------
# HELPERS
# ----------------------------
def _require_api_key():
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="PLANTNET_API_KEY hiányzik (Render Environment)")

def _httpx_client_sync() -> httpx.Client:
    timeout = httpx.Timeout(HTTP_TIMEOUT, connect=HTTP_CONNECT_TIMEOUT)
    limits = httpx.Limits(max_keepalive_connections=10, max_connections=20)
    return httpx.Client(
        timeout=timeout,
        limits=limits,
        follow_redirects=True,
        headers={"User-Agent": "PlantDiagnosisBot/1.2.0"},
    )

def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        try:
            return {"status": resp.status_code, "text": resp.text[:500]}
        except Exception:
            return {"status": resp.status_code, "non_json": True}

def _normalize_organs(organs: Optional[str]) -> List[str]:
    if not organs:
        return ["leaf"]
    parts = re.split(r"[,\s]+", str(organs).strip())
    parts = [p for p in parts if p]
    return parts or ["leaf"]

def _mode(payload: Dict[str, Any]) -> str:
    m = (payload.get("mode") or "learning").strip().lower()
    return m if m in ("learning", "expert") else "learning"

def _case_type(payload: Dict[str, Any]) -> str:
    ct = (payload.get("caseType") or "weed").strip().lower()
    return ct if ct in ("weed", "disease", "pest", "unknown") else "weed"

def _download_openai_file_sync(download_link: str) -> Tuple[bytes, str]:
    if not download_link:
        raise HTTPException(status_code=422, detail="Hiányzik: openaiFileIdRefs[].download_link")

    with _httpx_client_sync() as client:
        try:
            r = client.get(download_link)
        except httpx.TimeoutException as e:
            raise HTTPException(status_code=502, detail={"file_fetch_error": "timeout", "message": str(e)})
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail={"file_fetch_error": "request_error", "message": str(e)})

    if r.status_code >= 400:
        ct = r.headers.get("content-type")
        preview = None
        try:
            preview = r.text[:300] if (ct and ("text" in ct or "json" in ct)) else None
        except Exception:
            preview = None
        raise HTTPException(
            status_code=502,
            detail={
                "file_fetch_status": r.status_code,
                "file_fetch_content_type": ct,
                "file_fetch_preview": preview,
            },
        )

    data = r.content
    if not data or len(data) < 50:
        raise HTTPException(status_code=422, detail="A letöltött kép üres vagy túl kicsi.")

    mime = r.headers.get("content-type") or "image/jpeg"
    return data, mime

def _compact_species_response(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {"bestMatch": "ismeretlen", "confidence": {"top1_score": None, "level": "species"}, "topMatches": []}

    results = raw.get("results") or []
    best = "ismeretlen"
    best_score = None
    top = []

    for r in results[:6]:
        if not isinstance(r, dict):
            continue
        score = r.get("score")
        species = r.get("species") or {}
        sci = None
        if isinstance(species, dict):
            sci = species.get("scientificNameWithoutAuthor") or species.get("scientificName")
        if not sci:
            continue
        if best == "ismeretlen":
            best = sci
            best_score = score
        top.append({"name": sci, "score": float(score) if score is not None else None})

    return {
        "bestMatch": best,
        "confidence": {"top1_score": float(best_score) if best_score is not None else None, "level": "species"},
        "topMatches": top[:3],
    }

def _compact_disease_response(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {"bestMatch": "ismeretlen", "confidence": {"top1_score": None, "level": "disease_or_pest"}, "topMatches": []}

    results = raw.get("results") or raw.get("diseases") or []
    best = "ismeretlen"
    best_score = None
    top = []

    for r in results[:6]:
        if not isinstance(r, dict):
            continue
        score = r.get("score") or r.get("confidence")
        name = r.get("name")
        if not name:
            d = r.get("disease") if isinstance(r.get("disease"), dict) else None
            if d:
                name = d.get("name") or d.get("id")
        if not name:
            continue
        if best == "ismeretlen":
            best = name
            best_score = score
        top.append({"name": name, "score": float(score) if score is not None else None})

    return {
        "bestMatch": best,
        "confidence": {"top1_score": float(best_score) if best_score is not None else None, "level": "disease_or_pest"},
        "topMatches": top[:3],
    }

def _protocol_slots(case_type: str) -> List[Dict[str, str]]:
    if case_type == "disease":
        return [
            {"slot": "D1", "what": "Táblaszint/környezet (foltosság, terjedés)"},
            {"slot": "D2", "what": "Tünet távolról (teljes levél/szerv)"},
            {"slot": "D3", "what": "Tünet közelről (perem, udvar, színátmenet)"},
            {"slot": "D4", "what": "Fonák/sporuláció/penész (ha van)"},
            {"slot": "D5", "what": "Másik szerv: szár/kalász/termés (ahol legerősebb)"},
        ]
    if case_type == "pest":
        return [
            {"slot": "P1", "what": "Kártétel távolról (mintázat)"},
            {"slot": "P2", "what": "Kártétel közelről (lyuk/akna/szívogatás)"},
            {"slot": "P3", "what": "Kártevő/lárva/tojás (ha látható)"},
            {"slot": "P4", "what": "Rejtett hely: fonák/hajtáscsúcs"},
            {"slot": "P5", "what": "Bizonyító jel: ürülék/szövedék/járat"},
        ]
    # weed (default)
    return [
        {"slot": "W1", "what": "Egész növény felülnézet (rozetta/állomány)"},
        {"slot": "W2", "what": "Egész növény oldalnézet (szár/állás)"},
        {"slot": "W3", "what": "Levél közelről (alak, karéj, szél)"},
        {"slot": "W4", "what": "Levél fonák közelről (szőr, erezet)"},
        {"slot": "W5", "what": "Virág/termés közelről (ha nincs: tő/gyökérnyak)"},
    ]

def _photo_protocol(case_type: str, mode: str, images_used: int) -> Dict[str, Any]:
    slots = _protocol_slots(case_type)
    remaining = max(0, 5 - images_used)
    missing = slots[images_used:5] if images_used < 5 else []

    return {
        "mode": mode,
        "caseType": case_type,
        "rule": "1 eset = max 5 kép = 1 kredit",
        "imagesUsed": images_used,
        "imagesTarget": 5,
        "missingCount": remaining,
        "missingSlots": missing,
        "nextPhotos": [m["what"] for m in missing] if mode == "learning" else [],
    }

# ----------------------------
# ENDPOINTS
# ----------------------------
@app.get("/")
def root():
    return {"status": "ok"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/_build")
def build():
    return {"version": "1.2.0", "python": os.sys.version, "httpx": httpx.__version__}

@app.post("/diagnose_files")
def diagnose_files(payload: Dict[str, Any] = Body(...)):
    """
    GPT Actions: openaiFileIdRefs-ben 1..5 kép.
    Optional: mode: learning|expert, caseType: weed|disease|pest|unknown
    """
    _require_api_key()

    mode = _mode(payload)
    case_type = _case_type(payload)

    project = payload.get("project") or PLANTNET_PROJECT
    organs_list = _normalize_organs(payload.get("organs") or "leaf")  # egy organ vagy több; default leaf

    refs = payload.get("openaiFileIdRefs")
    if not refs or not isinstance(refs, list):
        raise HTTPException(status_code=422, detail="Hiányzik: openaiFileIdRefs (lista)")
    if len(refs) > 5:
        refs = refs[:5]

    # 1) letöltjük a képeket
    images: List[Tuple[str, bytes, str]] = []  # (filename, bytes, mime)
    for i, ref in enumerate(refs):
        if not isinstance(ref, dict):
            continue
        fn = ref.get("name") or f"image_{i+1}.jpg"
        b, mime = _download_openai_file_sync(ref.get("download_link"))
        images.append((fn, b, mime))

    if not images:
        raise HTTPException(status_code=422, detail="Nem sikerült képet letölteni openaiFileIdRefs alapján.")

    # organs kiosztás: ha 1 organ van, mindegyik kép azt kapja.
    # ha több organ jön (pl. leaf,flower), akkor ciklikusan kiosztjuk.
    organs_per_image: List[str] = []
    for i in range(len(images)):
        organs_per_image.append(organs_list[i % len(organs_list)] if organs_list else "leaf")

    # 2) Plant identify (PlantNet v2 identify/{project})
    identify_url = f"{PLANTNET_BASE_URL}/v2/identify/{project}"
    identify_params = [("api-key", PLANTNET_API_KEY)]

    identify_files = []
    for (fn, b, mime) in images:
        identify_files.append(("images", (fn, b, mime)))
    for o in organs_per_image:
        identify_files.append(("organs", (None, o)))

    with _httpx_client_sync() as client:
        try:
            r1 = client.post(identify_url, params=identify_params, files=identify_files)
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail={"plantnet_stage": "identify", "network_error": str(e)})

    if r1.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"plantnet_stage": "identify", "plantnet_status": r1.status_code, "plantnet_error": _safe_json(r1)},
        )

    # 3) Disease/Pest identify (PlantNet v2 diseases/identify) — NINCS project query!
    disease_url = f"{PLANTNET_BASE_URL}/v2/diseases/identify"
    disease_params = [("api-key", PLANTNET_API_KEY)]

    disease_files = []
    for (fn, b, mime) in images:
        disease_files.append(("images", (fn, b, mime)))

    with _httpx_client_sync() as client:
        try:
            r2 = client.post(disease_url, params=disease_params, files=disease_files)
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail={"plantnet_stage": "diseases", "network_error": str(e)})

    if r2.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"plantnet_stage": "diseases", "plantnet_status": r2.status_code, "plantnet_error": _safe_json(r2)},
        )

    plant_compact = _compact_species_response(_safe_json(r1))
    disease_compact = _compact_disease_response(_safe_json(r2))

    plant_score = (plant_compact.get("confidence") or {}).get("top1_score") or 0.0
    images_used = len(images)

    photo_protocol = _photo_protocol(case_type=case_type, mode=mode, images_used=images_used)

    # Ha learning módban alacsony score -> kifejezetten kérjük a hiányzó slotokat
    guidance = []
    if mode == "learning" and plant_score < 0.30 and images_used < 5:
        guidance = photo_protocol.get("nextPhotos") or []

    return {
        "plant": plant_compact,
        "diseaseOrPest": disease_compact,
        "summary": {
            "bestPlant": plant_compact.get("bestMatch"),
            "plantScore": plant_score,
            "bestIssue": disease_compact.get("bestMatch"),
            "issueScore": (disease_compact.get("confidence") or {}).get("top1_score"),
            "project": project,
            "organsPerImage": organs_per_image,
            "imagesCount": images_used,
        },
        "photoProtocol": photo_protocol,
        "guidance": guidance,
    }
