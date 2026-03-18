from fastapi import FastAPI, UploadFile, File, HTTPException
import requests
import shutil
import os
import uuid

app = FastAPI()

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "")
PLANTNET_PROJECT = "all"


@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "plant-diagnosis",
        "message": "3-route backend online"
    }


@app.get("/ping")
def ping():
    return {"ping": "pong"}


def save_upload_to_temp(upload: UploadFile) -> str:
    ext = os.path.splitext(upload.filename or "")[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".webp"]:
        ext = ".jpg"

    temp_path = f"/tmp/{uuid.uuid4()}{ext}"
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(upload.file, buffer)
    return temp_path


def call_plantnet_weed(image_path: str) -> dict:
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="Hiányzik a PLANTNET_API_KEY environment variable.")

    url = f"https://my-api.plantnet.org/v2/identify/{PLANTNET_PROJECT}?api-key={PLANTNET_API_KEY}"

    with open(image_path, "rb") as img:
        files = {
            "images": (os.path.basename(image_path), img, "image/jpeg")
        }
        data = {
            "organs": "auto"
        }
        response = requests.post(url, files=files, data=data, timeout=60)

    if response.status_code != 200:
        return {
            "status": "error",
            "status_code": response.status_code,
            "raw_response": response.text
        }

    payload = response.json()
    results = payload.get("results", [])

    if not results:
        return {
            "status": "ok",
            "commentary": "Nincs találat.",
            "context_flags": {"no_result": True},
            "raw": payload
        }

    top = results[0]
    score = top.get("score", 0)
    species = top.get("species", {})
    species_name = species.get("scientificNameWithoutAuthor", "Ismeretlen")

    return {
        "status": "ok",
        "mode": "weed",
        "species": species_name,
        "score": score,
        "commentary": f"Felismert növény: {species_name} ({round(score * 100)}%)",
        "context_flags": {},
        "raw": payload
    }


def call_plantnet_disease(image_path: str) -> dict:
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="Hiányzik a PLANTNET_API_KEY environment variable.")

    url = f"https://my-api.plantnet.org/v2/diseases/identify?api-key={PLANTNET_API_KEY}"

    with open(image_path, "rb") as img:
        files = {
            "images": (os.path.basename(image_path), img, "image/jpeg")
        }
        data = {
            "organs": "auto"
        }
        response = requests.post(url, files=files, data=data, timeout=60)

    if response.status_code != 200:
        return {
            "status": "error",
            "status_code": response.status_code,
            "raw_response": response.text
        }

    payload = response.json()
    results = payload.get("results", [])

    if not results:
        return {
            "status": "ok",
            "mode": "disease",
            "commentary": "Nincs találat.",
            "context_flags": {"no_result": True},
            "raw": payload
        }

    top = results[0]
    score = top.get("score", 0)
    label = top.get("description") or top.get("name") or "Ismeretlen"

    return {
        "status": "ok",
        "mode": "disease",
        "label": label,
        "score": score,
        "commentary": f"Lehetséges betegség: {label} ({round(score * 100)}%)",
        "context_flags": {},
        "raw": payload
    }


def call_plantnet_pest(image_path: str) -> dict:
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="Hiányzik a PLANTNET_API_KEY environment variable.")

    url = f"https://my-api.plantnet.org/v2/diseases/identify?api-key={PLANTNET_API_KEY}"

    with open(image_path, "rb") as img:
        files = {
            "images": (os.path.basename(image_path), img, "image/jpeg")
        }
        data = {
            "organs": "auto"
        }
        response = requests.post(url, files=files, data=data, timeout=60)

    if response.status_code != 200:
        return {
            "status": "error",
            "status_code": response.status_code,
            "raw_response": response.text
        }

    payload = response.json()
    results = payload.get("results", [])

    if not results:
        return {
            "status": "ok",
            "mode": "pest",
            "commentary": "Nincs találat.",
            "context_flags": {"no_result": True},
            "raw": payload
        }

    top = results[0]
    score = top.get("score", 0)
    label = top.get("description") or top.get("name") or "Ismeretlen"

    return {
        "status": "ok",
        "mode": "pest",
        "label": label,
        "score": score,
        "commentary": f"Lehetséges kártevő: {label} ({round(score * 100)}%)",
        "context_flags": {},
        "raw": payload
    }


@app.post("/analyze-weed")
async def analyze_weed(file: UploadFile = File(...)):
    temp_path = None
    try:
        temp_path = save_upload_to_temp(file)
        return call_plantnet_weed(temp_path)
    except requests.Timeout:
        raise HTTPException(status_code=504, detail="PlantNet időtúllépés.")
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Hálózati hiba: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Belső hiba: {str(e)}")
    finally:
        try:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass


@app.post("/analyze-disease")
async def analyze_disease(file: UploadFile = File(...)):
    temp_path = None
    try:
        temp_path = save_upload_to_temp(file)
        return call_plantnet_disease(temp_path)
    except requests.Timeout:
        raise HTTPException(status_code=504, detail="PlantNet időtúllépés.")
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Hálózati hiba: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Belső hiba: {str(e)}")
    finally:
        try:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass


@app.post("/analyze-pest")
async def analyze_pest(file: UploadFile = File(...)):
    temp_path = None
    try:
        temp_path = save_upload_to_temp(file)
        return call_plantnet_pest(temp_path)
    except requests.Timeout:
        raise HTTPException(status_code=504, detail="PlantNet időtúllépés.")
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Hálózati hiba: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Belső hiba: {str(e)}")
    finally:
        try:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass
