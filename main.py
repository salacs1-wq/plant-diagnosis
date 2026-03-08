import os
import json
from typing import Any, Dict, List, Optional, Tuple
from recommend_logic import find_products_by_crop_and_weed

import httpx

from fastapi import FastAPI, Body, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware

from result_mapper import map_plantnet_result
from weeds_logic import build_weed_summary
from init_db import init_db
from import_master_products import import_products
from import_product_usage import import_product_usage
from import_weed_species import import_weed_species
from import_weed_master import import_weed_master

# =========================
# Config
# =========================
APP_VERSION = os.getenv("APP_VERSION", "1.3.2")

# Your internal API key (optional gate). If empty => no auth check.
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "").strip()

# PlantNet
PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_BASE_URL = os.getenv("PLANTNET_BASE_URL", "https://my-api.plantnet.org").rstrip("/")
PLANTNET_DEFAULT_PROJECT = os.getenv("PLANTNET_PROJECT", "weurope")  # weurope / all / k-middle-europe etc.

# HTTP
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "60"))
MAX_IMAGES = int(os.getenv("MAX_IMAGES", "5"))
MAX_IMAGE_BYTES = int(os.getenv("MAX_IMAGE_BYTES", str(10 * 1024 * 1024)))  # 10 MB per image


# =========================
# App
# =========================
app = FastAPI(
    title="Plant Diagnosis API",
    version=APP_VERSION,
    description="PlantNet-based identification with OpenAI file-ref download support and manual upload endpoint.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup():
    app.state.http = httpx.AsyncClient(timeout=HTTP_TIMEOUT)

    init_db()
    import_products()
    import_product_usage()
    import_weed_species()
    import_weed_master()


@app.on_event("shutdown")
async def _shutdown():
    client: httpx.AsyncClient = app.state.http
    await client.aclose()


def _require_internal_key(x_api_key: Optional[str]):
    if INTERNAL_API_KEY:
        if not x_api_key or x_api_key.strip() != INTERNAL_API_KEY:
            raise HTTPException(status_code=401, detail="Missing/invalid X-API-Key.")


def _require_plantnet_key():
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="Missing PLANTNET_API_KEY on server.")


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return {"text": resp.text[:2000]}


def _normalize_organs(organs: Optional[str]) -> List[str]:
    """
    PlantNet expects multiple organs as repeated query param: organs=leaf&organs=flower
    Input can be:
      - None => no organs sent
      - "leaf"
      - "leaf,flower"
    """
    if not organs:
        return []
    raw = str(organs).strip()
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts


def _plantnet_params(organs_list: List[str]) -> List[Tuple[str, str]]:
    params: List[Tuple[str, str]] = [("api-key", PLANTNET_API_KEY)]
    for o in organs_list:
        params.append(("organs", o))
    return params


async def _download_image(url: str) -> Tuple[bytes, str]:
    """
    Download an image from a signed URL (OpenAI file download_link).
    Returns (bytes, mime_type).
    """
    client: httpx.AsyncClient = app.state.http
    r = await client.get(url, follow_redirects=True)
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail={"download_status": r.status_code, "download_error": _safe_json(r)})
    content = r.content
    if not content or len(content) < 50:
        raise HTTPException(status_code=422, detail="Downloaded image is empty/too small.")
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail=f"Image too large (> {MAX_IMAGE_BYTES} bytes).")
    mime = r.headers.get("content-type", "image/jpeg").split(";")[0].strip() or "image/jpeg"
    return content, mime


def _compact_species_response(plantnet_json: Dict[str, Any], top_n: int = 5) -> Dict[str, Any]:
    """
    PlantNet response -> compact list.
    We keep PlantNet score as the single source of truth for score.
    """
    results = plantnet_json.get("results") or []
    compact: List[Dict[str, Any]] = []

    for item in results[:top_n]:
        species = item.get("species") or {}
        score = float(item.get("score") or 0.0)

        sci = species.get("scientificNameWithoutAuthor") or species.get("scientificName") or ""
        family = (species.get("family") or {}).get("scientificNameWithoutAuthor") or ""
        common_names = species.get("commonNames") or []
        gbif = species.get("gbif") or {}
        gbif_id = gbif.get("id")

        compact.append(
            {
                "scientificName": sci,
                "family": family,
                "commonNames": common_names,
                "score": score,
                "scorePct": round(score * 100, 1),
                "gbifId": gbif_id,
            }
        )

    return {
        "plantnetProject": plantnet_json.get("query", {}).get("project") or None,
        "results": compact,
        "raw": None,
    }


async def _plantnet_identify(
    images: List[Tuple[str, bytes, str]],
    project: str,
    organs: Optional[str],
) -> Dict[str, Any]:
    """
    Async call to PlantNet identify endpoint with multiple images.
    Retry once automatically if the first response is server-side (5xx).
    """
    _require_plantnet_key()

    organs_list = _normalize_organs(organs)
    url = f"{PLANTNET_BASE_URL}/v2/identify/{project}"
    params = _plantnet_params(organs_list)

    files = []
    for (fname, bts, mime) in images:
        files.append(("images", (fname or "image.jpg", bts, mime or "image/jpeg")))

    client: httpx.AsyncClient = app.state.http
    last_error: Optional[httpx.Response] = None

    for attempt in range(2):
        r = await client.post(url, params=params, files=files)
        last_error = r
        if r.status_code < 500:
            break

    if last_error is None:
        raise HTTPException(status_code=502, detail="PlantNet request failed before receiving a response.")

    if last_error.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={
                "plantnet_status": last_error.status_code,
                "plantnet_error": _safe_json(last_error),
                "plantnet_url": url,
                "project": project,
                "organs": organs_list,
            },
        )

    return last_error.json()


# =========================
# Endpoints
# =========================
@app.get("/")
async def root():
    return {
        "status": "ok",
        "version": APP_VERSION,
        "endpoints": ["/health", "/version", "/diagnose_files", "/diagnose_upload", "/docs", "/openapi.json"],
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": APP_VERSION,
        "plantnet_key_set": bool(PLANTNET_API_KEY),
        "default_project": PLANTNET_DEFAULT_PROJECT,
    }


@app.get("/version")
async def version():
    return {"appVersion": APP_VERSION}

@app.get("/products_test")
async def products_test():
    from db import get_connection

@app.get("/product_usage_test")
async def product_usage_test():
    from db import get_connection

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM product_usage LIMIT 10")
    rows = cur.fetchall()
    conn.close()

    return {
        "count": len(rows),
        "items": [dict(row) for row in rows]
    }

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM products LIMIT 10")
    rows = cur.fetchall()
    conn.close()

    return {
        "count": len(rows),
        "items": [dict(row) for row in rows]
    }


@app.post("/diagnose_files")
async def diagnose_files(
    payload: Dict[str, Any] = Body(
        ...,
        example={
            "openaiFileIdRefs": [
                {
                    "name": "field_image.jpg",
                    "id": "file_...",
                    "mime_type": "image/jpeg",
                    "download_link": "https://....",
                }
            ],
            "project": "k-middle-europe",
            "mode": "expert",
            "caseType": "weed",
        },
    ),
    x_api_key: Optional[str] = None,
):
    """
    Main endpoint used by GPT Actions.
    Expects OpenAI file refs with signed download_link.
    """
    _require_internal_key(x_api_key)

    openai_refs = payload.get("openaiFileIdRefs") or []
    if not isinstance(openai_refs, list) or len(openai_refs) == 0:
        raise HTTPException(
            status_code=422,
            detail="openaiFileIdRefs is missing/empty. Attach at least 1 image in the chat message.",
        )

    project = (payload.get("project") or PLANTNET_DEFAULT_PROJECT) or PLANTNET_DEFAULT_PROJECT
    organs = payload.get("organs")  # optional
    mode = (payload.get("mode") or "expert").strip()
    case_type = (payload.get("caseType") or "weed").strip()

    images: List[Tuple[str, bytes, str]] = []
    for ref in openai_refs[:MAX_IMAGES]:
        if not isinstance(ref, dict):
            continue
        dl = (ref.get("download_link") or "").strip()
        if not dl:
            continue
        name = (ref.get("name") or "image.jpg").strip()
        content, mime = await _download_image(dl)
        images.append((name, content, mime))

    if not images:
        raise HTTPException(status_code=422, detail="No valid download_link found in openaiFileIdRefs.")

    plantnet_raw = await _plantnet_identify(images=images, project=project, organs=organs)
    
    compact = _compact_species_response(plantnet_raw, top_n=5)

    mapped = map_plantnet_result(plantnet_raw)

    weed_summary = build_weed_summary(mapped)

    return {
        "project": project,
        "organs": organs,
        "mode": mode,
        "caseType": case_type,
        "topN": 5,
        "plantnet": compact,
        "weedSummary": weed_summary,
    }


@app.post("/diagnose_upload")
async def diagnose_upload(
    image: UploadFile = File(...),
    project: str = Form(PLANTNET_DEFAULT_PROJECT),
    mode: str = Form("expert"),
    caseType: str = Form("weed"),
):
    """
    Manual upload endpoint for Swagger / browser testing.
    """

    content = await image.read()

    if not content or len(content) < 50:
        raise HTTPException(status_code=422, detail="Uploaded image is empty/too small.")

    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Image too large (> {MAX_IMAGE_BYTES} bytes)."
        )

    mime = image.content_type or "image/jpeg"
    name = image.filename or "image.jpg"

    plantnet_raw = await _plantnet_identify(
        images=[(name, content, mime)],
        project=project,
        organs=None,
    )

    compact = _compact_species_response(plantnet_raw, top_n=5)
    mapped = map_plantnet_result(plantnet_raw)
    weed_summary = build_weed_summary(mapped)

    return {
        "project": project,
        "mode": mode,
        "caseType": caseType,
        "topN": 5,
        "plantnet": compact,
        "weedSummary": weed_summary,
    }
@app.get("/weed_species_test")
async def weed_species_test():

    from db import get_connection

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT * FROM product_weed_species LIMIT 10")

    rows = cur.fetchall()

    conn.close()

    return {
        "count": len(rows),
        "items": [dict(r) for r in rows]
    }


@app.get("/recommend_test")
async def recommend_test(crop: str, weed_latin: str):

    items = find_products_by_crop_and_weed(crop, weed_latin)

    return {
        "count": len(items),
        "items": items
    }
