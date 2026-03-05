from __future__ import annotations

import io
import os
import time
from typing import Any, Dict, List, Literal, Optional

import requests
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

try:
    from PIL import Image
except Exception:
    Image = None  # pillow optional


Mode = Literal["weed", "disease", "pest", "crop", "auto"]
# --- Szántóföldi gyom adatbázis (HU) ---
# crop_tags: "wheat" (kalászos), "rape" (repce), "maize" (kukorica), "sunflower", "soy", "beet" (cukorrépa), "general"
# group: "grass" (egyszikű), "broadleaf" (kétszikű), "sedge" (sás/féle), "horsetail" (zsurló)
FIELD_WEEDS_HU = [
    # EGYSZIKŰ / FŰFÉLÉK
    {"latin":"Apera spica-venti","hu":"nagy széltippan","group":"grass","crop_tags":["wheat","general"]},
    {"latin":"Alopecurus myosuroides","hu":"nagy rókafarkfű","group":"grass","crop_tags":["wheat","general"]},
    {"latin":"Alopecurus pratensis","hu":"réti rókafarkfű","group":"grass","crop_tags":["general"]},
    {"latin":"Anisantha sterilis","hu":"meddő rozsnok","group":"grass","crop_tags":["wheat","general"]},  # = Bromus sterilis
    {"latin":"Bromus sterilis","hu":"meddő rozsnok","group":"grass","crop_tags":["wheat","general"]},
    {"latin":"Bromus tectorum","hu":"tetőrozsnok","group":"grass","crop_tags":["wheat","general"]},
    {"latin":"Bromus secalinus","hu":"rozs-rozsnok","group":"grass","crop_tags":["wheat","general"]},
    {"latin":"Bromus hordeaceus","hu":"puha rozsnok","group":"grass","crop_tags":["general"]},
    {"latin":"Bromus commutatus","hu":"váltakozó rozsnok","group":"grass","crop_tags":["general"]},
    {"latin":"Bromus japonicus","hu":"japán rozsnok","group":"grass","crop_tags":["general"]},
    {"latin":"Lolium perenne","hu":"angolperje","group":"grass","crop_tags":["general"]},
    {"latin":"Lolium multiflorum","hu":"olaszperje","group":"grass","crop_tags":["general"]},
    {"latin":"Lolium rigidum","hu":"merev perje","group":"grass","crop_tags":["wheat","general"]},
    {"latin":"Avena fatua","hu":"héla zab","group":"grass","crop_tags":["wheat","general"]},
    {"latin":"Avena sterilis","hu":"meddő zab","group":"grass","crop_tags":["wheat","general"]},
    {"latin":"Phalaris minor","hu":"kis kanáriköles","group":"grass","crop_tags":["wheat","general"]},
    {"latin":"Phalaris paradoxa","hu":"csodás kanáriköles","group":"grass","crop_tags":["wheat","general"]},
    {"latin":"Poa annua","hu":"egynyári perje","group":"grass","crop_tags":["wheat","general"]},
    {"latin":"Poa trivialis","hu":"réti perje","group":"grass","crop_tags":["wheat","general"]},
    {"latin":"Poa pratensis","hu":"réti perje","group":"grass","crop_tags":["general"]},
    {"latin":"Poa bulbosa","hu":"gumós perje","group":"grass","crop_tags":["general"]},
    {"latin":"Vulpia myuros","hu":"egérfarkú perje","group":"grass","crop_tags":["wheat","general"]},
    {"latin":"Vulpia bromoides","hu":"rozsnok-perje","group":"grass","crop_tags":["wheat","general"]},
    {"latin":"Festuca arundinacea","hu":"nádas csenkesz","group":"grass","crop_tags":["general"]},
    {"latin":"Festuca rubra","hu":"vörös csenkesz","group":"grass","crop_tags":["general"]},
    {"latin":"Festuca pratensis","hu":"réti csenkesz","group":"grass","crop_tags":["general"]},
    {"latin":"Agropyron repens","hu":"tarackbúza","group":"grass","crop_tags":["general"]},  # = Elymus repens
    {"latin":"Elymus repens","hu":"tarackbúza","group":"grass","crop_tags":["general"]},
    {"latin":"Cynodon dactylon","hu":"csillagpázsit","group":"grass","crop_tags":["general"]},
    {"latin":"Digitaria sanguinalis","hu":"vérpiros muhar","group":"grass","crop_tags":["maize","sunflower","soy","general"]},
    {"latin":"Digitaria ischaemum","hu":"sovány muhar","group":"grass","crop_tags":["maize","sunflower","soy","general"]},
    {"latin":"Echinochloa crus-galli","hu":"kakaslábfű","group":"grass","crop_tags":["maize","sunflower","soy","beet","general"]},
    {"latin":"Setaria viridis","hu":"zöld muhar","group":"grass","crop_tags":["maize","sunflower","soy","beet","general"]},
    {"latin":"Setaria pumila","hu":"sárga muhar","group":"grass","crop_tags":["maize","sunflower","soy","beet","general"]},
    {"latin":"Setaria verticillata","hu":"ragadós muhar","group":"grass","crop_tags":["maize","sunflower","soy","general"]},
    {"latin":"Sorghum halepense","hu":"fenyércirok","group":"grass","crop_tags":["maize","sunflower","soy","beet","general"]},
    {"latin":"Panicum miliaceum","hu":"köles árvakelés / vadköles","group":"grass","crop_tags":["maize","sunflower","general"]},
    {"latin":"Panicum capillare","hu":"szőrös köles","group":"grass","crop_tags":["maize","sunflower","general"]},
    {"latin":"Hordeum murinum","hu":"egérárpa","group":"grass","crop_tags":["general"]},
    {"latin":"Brachypodium distachyon","hu":"kétsoros szálkaperje","group":"grass","crop_tags":["wheat","general"]},
    {"latin":"Brachypodium pinnatum","hu":"tarackos szálkaperje","group":"grass","crop_tags":["general"]},
    {"latin":"Agrostis stolonifera","hu":"tarackos tippan","group":"grass","crop_tags":["general"]},
    {"latin":"Agrostis capillaris","hu":"vörös tippan","group":"grass","crop_tags":["general"]},
    {"latin":"Phleum pratense","hu":"réti komócsin","group":"grass","crop_tags":["general"]},
    {"latin":"Holcus lanatus","hu":"pelyhes perje","group":"grass","crop_tags":["general"]},

    # SÁS / ZSURLÓ
    {"latin":"Cyperus esculentus","hu":"földi mandula (sárga zsombor?)","group":"sedge","crop_tags":["maize","sunflower","general"]},
    {"latin":"Equisetum arvense","hu":"mezei zsurló","group":"horsetail","crop_tags":["general"]},

    # KÉTSZIKŰEK – ŐSZI/KORA TAVASZI GYOMOK (kalászos/repce)
    {"latin":"Capsella bursa-pastoris","hu":"pásztortáska","group":"broadleaf","crop_tags":["wheat","rape","general"]},
    {"latin":"Thlaspi arvense","hu":"mezei tarsóka","group":"broadleaf","crop_tags":["wheat","rape","general"]},
    {"latin":"Cardaria draba","hu":"magyar zsázsa","group":"broadleaf","crop_tags":["general"]},  # = Lepidium draba
    {"latin":"Lepidium draba","hu":"magyar zsázsa","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Descurainia sophia","hu":"büdös zsombor","group":"broadleaf","crop_tags":["wheat","rape","general"]},
    {"latin":"Sisymbrium officinale","hu":"bodros bükköny? (orvosi zsombor)","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Sinapis arvensis","hu":"vadrepce","group":"broadleaf","crop_tags":["wheat","rape","maize","sunflower","general"]},
    {"latin":"Raphanus raphanistrum","hu":"vadretek","group":"broadleaf","crop_tags":["wheat","rape","maize","sunflower","general"]},
    {"latin":"Matricaria chamomilla","hu":"orvosi székfű","group":"broadleaf","crop_tags":["wheat","general"]},
    {"latin":"Tripleurospermum inodorum","hu":"ebszékfű","group":"broadleaf","crop_tags":["wheat","rape","general"]},
    {"latin":"Anthemis arvensis","hu":"mezei pipitér","group":"broadleaf","crop_tags":["wheat","general"]},
    {"latin":"Papaver rhoeas","hu":"pipacs","group":"broadleaf","crop_tags":["wheat","rape","general"]},
    {"latin":"Fumaria officinalis","hu":"orvosi füstike","group":"broadleaf","crop_tags":["wheat","rape","general"]},
    {"latin":"Viola arvensis","hu":"mezei árvácska","group":"broadleaf","crop_tags":["wheat","rape","general"]},
    {"latin":"Viola tricolor","hu":"háromszínű árvácska","group":"broadleaf","crop_tags":["wheat","rape","general"]},
    {"latin":"Stellaria media","hu":"tyúkhúr","group":"broadleaf","crop_tags":["wheat","rape","general"]},
    {"latin":"Cerastium fontanum","hu":"egérszarvúfű","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Veronica persica","hu":"perzsa veronika","group":"broadleaf","crop_tags":["wheat","rape","general"]},
    {"latin":"Veronica hederifolia","hu":"borostyánlevelű veronika","group":"broadleaf","crop_tags":["wheat","rape","general"]},
    {"latin":"Veronica arvensis","hu":"mezei veronika","group":"broadleaf","crop_tags":["wheat","general"]},
    {"latin":"Veronica polita","hu":"fényes veronika","group":"broadleaf","crop_tags":["wheat","rape","general"]},
    {"latin":"Veronica agrestis","hu":"kerti veronika","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Lamium purpureum","hu":"piros árvacsalán","group":"broadleaf","crop_tags":["wheat","rape","general"]},
    {"latin":"Lamium amplexicaule","hu":"szárölelő árvacsalán","group":"broadleaf","crop_tags":["wheat","rape","general"]},
    {"latin":"Lamium album","hu":"fehér árvacsalán","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Galium aparine","hu":"ragadós galaj","group":"broadleaf","crop_tags":["wheat","rape","general"]},
    {"latin":"Aphanes arvensis","hu":"mezei pásztortáska? (apró füzike)","group":"broadleaf","crop_tags":["wheat","general"]},
    {"latin":"Centaurea cyanus","hu":"búzavirág","group":"broadleaf","crop_tags":["wheat","general"]},
    {"latin":"Myosotis arvensis","hu":"mezei nefelejcs","group":"broadleaf","crop_tags":["wheat","general"]},
    {"latin":"Lithospermum arvense","hu":"mezei kővirág","group":"broadleaf","crop_tags":["wheat","general"]},
    {"latin":"Consolida regalis","hu":"kerti szarkaláb","group":"broadleaf","crop_tags":["wheat","general"]},
    {"latin":"Ranunculus arvensis","hu":"mezei boglárka","group":"broadleaf","crop_tags":["wheat","general"]},
    {"latin":"Geranium dissectum","hu":"vágottlevelű gólyaorr","group":"broadleaf","crop_tags":["wheat","rape","general"]},
    {"latin":"Geranium molle","hu":"puha gólyaorr","group":"broadleaf","crop_tags":["wheat","general"]},
    {"latin":"Geranium pusillum","hu":"apró gólyaorr","group":"broadleaf","crop_tags":["wheat","general"]},
    {"latin":"Erodium cicutarium","hu":"büröklevelű gémorr","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Spergula arvensis","hu":"mezei tyúkhúr? (mezei csillaghúr)","group":"broadleaf","crop_tags":["wheat","general"]},
    {"latin":"Scleranthus annuus","hu":"egynyári csomós","group":"broadleaf","crop_tags":["general"]},

    # KÉTSZIKŰEK – TAVASZI/NYÁRI GYOMOK (kapásokban)
    {"latin":"Chenopodium album","hu":"fehér libatop","group":"broadleaf","crop_tags":["maize","sunflower","soy","beet","general"]},
    {"latin":"Chenopodium hybridum","hu":"büdös libatop","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Atriplex patula","hu":"lapulevelű laboda","group":"broadleaf","crop_tags":["maize","sunflower","general"]},
    {"latin":"Atriplex tatarica","hu":"tatár laboda","group":"broadleaf","crop_tags":["maize","sunflower","general"]},
    {"latin":"Amaranthus retroflexus","hu":"szőrös disznóparéj","group":"broadleaf","crop_tags":["maize","sunflower","soy","beet","general"]},
    {"latin":"Amaranthus powellii","hu":"Powell-disznóparéj","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Amaranthus blitoides","hu":"fekvő disznóparéj","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Ambrosia artemisiifolia","hu":"parlagfű","group":"broadleaf","crop_tags":["maize","sunflower","soy","general"]},
    {"latin":"Xanthium strumarium","hu":"csattanó maszlag? (szerbtövis)","group":"broadleaf","crop_tags":["maize","sunflower","general"]},
    {"latin":"Datura stramonium","hu":"csattanó maszlag","group":"broadleaf","crop_tags":["maize","sunflower","soy","general"]},
    {"latin":"Abutilon theophrasti","hu":"selyemmályva","group":"broadleaf","crop_tags":["maize","sunflower","soy","general"]},
    {"latin":"Conyza canadensis","hu":"kanadai betyárkóró","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Erigeron annuus","hu":"egynyári seprence","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Artemisia vulgaris","hu":"fekete üröm","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Cirsium arvense","hu":"mezei acat","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Sonchus arvensis","hu":"mezei csorbóka","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Sonchus oleraceus","hu":"kerti csorbóka","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Lactuca serriola","hu":"keszegsaláta","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Taraxacum officinale","hu":"gyermekláncfű","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Crepis sancta","hu":"szent-zörgőfű","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Crepis tectorum","hu":"tető zörgőfű","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Rumex obtusifolius","hu":"lórom","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Rumex crispus","hu":"fodros lórom","group":"broadleaf","crop_tags":["general"]},

    # KESERŰFŰFÉLÉK / POLYGONACEAE
    {"latin":"Polygonum aviculare","hu":"madárkeserűfű","group":"broadleaf","crop_tags":["maize","sunflower","soy","beet","general"]},
    {"latin":"Persicaria maculosa","hu":"foltos keserűfű","group":"broadleaf","crop_tags":["maize","sunflower","soy","beet","general"]},  # = Polygonum persicaria
    {"latin":"Persicaria lapathifolia","hu":"baracklevelű keserűfű","group":"broadleaf","crop_tags":["maize","sunflower","soy","beet","general"]},
    {"latin":"Fallopia convolvulus","hu":"sövénykeserűfű","group":"broadleaf","crop_tags":["maize","sunflower","soy","general"]},
    {"latin":"Polygonum amphibium","hu":"vízi/mocsári keserűfű","group":"broadleaf","crop_tags":["general"]},

    # LIBATOP/DISZNÓPARÉJ mellett még
    {"latin":"Portulaca oleracea","hu":"kövér porcsin","group":"broadleaf","crop_tags":["maize","sunflower","general"]},
    {"latin":"Mercurialis annua","hu":"egynyári kutyatej","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Euphorbia helioscopia","hu":"napfényes kutyatej","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Euphorbia peplus","hu":"kerti kutyatej","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Euphorbia esula","hu":"farkaskutyatej","group":"broadleaf","crop_tags":["general"]},

    # Fészkesek / egyéb gyakoriak
    {"latin":"Galeopsis tetrahit","hu":"borzas szurok","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Galeopsis speciosa","hu":"széleslevelű szurok","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Stachys annua","hu":"egynyári tisztesfű","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Solanum nigrum","hu":"csucsor (fekete csucsor)","group":"broadleaf","crop_tags":["maize","sunflower","soy","general"]},
    {"latin":"Solanum physalifolium","hu":"szőrös csucsor","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Physalis angulata","hu":"földicseresznye (kivadulva)","group":"broadleaf","crop_tags":["general"]},

    # Szulák / szádor / stb.
    {"latin":"Convolvulus arvensis","hu":"mezei szulák","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Cuscuta campestris","hu":"mezei aranka","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Orobanche cumana","hu":"napraforgó szádor","group":"broadleaf","crop_tags":["sunflower"]},

    # Pillangósok (gyakori gyomok)
    {"latin":"Vicia hirsuta","hu":"szőrös bükköny","group":"broadleaf","crop_tags":["wheat","rape","general"]},
    {"latin":"Vicia sativa","hu":"vetési bükköny","group":"broadleaf","crop_tags":["wheat","general"]},
    {"latin":"Vicia villosa","hu":"szöszös bükköny","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Lathyrus tuberosus","hu":"gumós lednek","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Medicago lupulina","hu":"komlós lucerna","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Medicago sativa","hu":"lucerna (árvakelés/gyom)","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Trifolium repens","hu":"fehér here","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Trifolium pratense","hu":"vörös here","group":"broadleaf","crop_tags":["general"]},

    # Keresztesvirágúak egy része (repce/kalászos)
    {"latin":"Camelina microcarpa","hu":"kis termésű repcsényretek? (hamis len)","group":"broadleaf","crop_tags":["wheat","rape","general"]},
    {"latin":"Erysimum cheiranthoides","hu":"repceboglárka? (festő csormolya)","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Neslia paniculata","hu":"bogas repcsény","group":"broadleaf","crop_tags":["wheat","rape","general"]},

    # Egyéb gyakoriak
    {"latin":"Urtica urens","hu":"kis csalán","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Urtica dioica","hu":"nagy csalán","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Plantago major","hu":"nagy útifű","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Plantago lanceolata","hu":"lándzsás útifű","group":"broadleaf","crop_tags":["general"]},
    {"latin":"Polygonatum multiflorum","hu":"salamonpecsét (ritka szántón)","group":"broadleaf","crop_tags":["general"]},  # bent hagyható, de szűrés majd kidobja crop alapján
]

# Gyors index: latin név -> rekord
FIELD_WEEDS_INDEX = {w["latin"].lower(): w for w in FIELD_WEEDS_HU}
APP_NAME = "plant-diagnosis"
APP_VERSION = "1.0.0"

app = FastAPI(title="Plant Diagnosis API", version=APP_VERSION)

# CORS
allowed_origins = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in allowed_origins] if allowed_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["meta"])
def root() -> Dict[str, Any]:
    return {
        "name": APP_NAME,
        "version": APP_VERSION,
        "status": "ok",
        "endpoints": ["/health", "/v1/diagnose", "/docs", "/openapi.json"],
    }


@app.get("/health", tags=["meta"])
def health() -> Dict[str, Any]:
    return {"status": "ok", "ts": int(time.time())}


def _image_debug_info(image_bytes: bytes) -> Dict[str, Any]:
    info: Dict[str, Any] = {"bytes": len(image_bytes)}
    if Image is None:
        info["pil"] = "not_installed"
        return info
    try:
        img = Image.open(io.BytesIO(image_bytes))
        info.update(
            {
                "pil": "ok",
                "format": img.format,
                "size": {"width": img.size[0], "height": img.size[1]},
                "mode": img.mode,
            }
        )
    except Exception as e:
        info["pil"] = "error"
        info["pil_error"] = str(e)
    return info


def call_plantnet(image_bytes: bytes) -> Dict[str, Any]:
    api_key = os.getenv("PLANTNET_API_KEY", "").strip()
    base_url = os.getenv("PLANTNET_BASE_URL", "https://my-api.plantnet.org").strip().rstrip("/")
    project = os.getenv("PLANTNET_PROJECT", "k-middle-europe").strip()

    if not api_key:
        raise HTTPException(status_code=500, detail="PLANTNET_API_KEY nincs beállítva a Render env-ben.")

    # API kulcs query paraméterben!
    url = f"{base_url}/v2/identify/{project}?api-key={api_key}"

    files = {
        "images": ("image.jpg", image_bytes, "image/jpeg"),
    }

    try:
        r = requests.post(url, files=files, timeout=60)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"PlantNet hívási hiba: {e}")

    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"PlantNet HTTP {r.status_code}: {r.text[:500]}")

    try:
        return r.json()
    except Exception:
        raise HTTPException(status_code=502, detail="PlantNet válasz nem JSON.")


def simplify_plantnet_response(raw: Dict[str, Any], top_k: int = 5) -> Dict[str, Any]:
    results = raw.get("results", []) or []
    simple: List[Dict[str, Any]] = []

    for item in results[:top_k]:
        species = item.get("species", {}) or {}
        sci = species.get("scientificNameWithoutAuthor") or species.get("scientificName") or "unknown"
        common = species.get("commonNames") or []
        simple.append(
            {
                "scientific_name": sci,
                "confidence": item.get("score", None),
                "common_names": common[:5],
            }
        )

    return {
        "engine": "plantnet",
        "project": raw.get("project") or os.getenv("PLANTNET_PROJECT", "k-middle-europe"),
        "top_k": top_k,
        "candidates": simple,
    }


@app.post("/v1/diagnose", tags=["diagnosis"])
async def diagnose(
    mode: Mode = Form("auto"),
    image: UploadFile = File(...),
    note: Optional[str] = Form(None),
    debug: bool = Form(False),
) -> JSONResponse:
    if mode not in ("weed", "disease", "pest", "crop", "auto"):
        raise HTTPException(status_code=400, detail="Érvénytelen mode.")

    image_bytes = await image.read()
    if not image_bytes or len(image_bytes) < 50:
        raise HTTPException(status_code=400, detail="Üres / túl kicsi kép.")

    raw = call_plantnet(image_bytes)
    result = simplify_plantnet_response(raw, top_k=5)

    payload: Dict[str, Any] = {
        "ok": True,
        "request": {
            "mode": mode,
            "filename": image.filename,
            "content_type": image.content_type,
            "note": note,
        },
        "result": result,
    }

    if debug:
        payload["debug"] = _image_debug_info(image_bytes)

    return JSONResponse(payload)
