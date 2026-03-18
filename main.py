from fastapi import FastAPI, File, UploadFile, HTTPException
import requests
import shutil
import uuid
import os

app = FastAPI()

API_KEY = os.getenv("PLANTNET_API_KEY", "")
PROJECT = "all"


@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "plant-diagnosis",
        "message": "Railway backend online"
    }


@app.get("/ping")
def ping():
    return {"ping": "pong"}


@app.post("/analyze")
async def analyze(file: UploadFile = File(...), mode: str = "weed"):
    if not API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Hiányzik a PLANTNET_API_KEY environment variable."
        )

    mode = (mode or "weed").strip().lower()

    if mode not in ["weed", "disease", "pest"]:
        mode = "weed"

    file_id = str(uuid.uuid4())
    original_name = file.filename or "upload.jpg"
    ext = os.path.splitext(original_name)[1].lower()

    if ext not in [".jpg", ".jpeg", ".png", ".webp"]:
        ext = ".jpg"

    file_path = f"temp_{file_id}{ext}"

    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        if mode == "weed":
            url = f"https://my-api.plantnet.org/v2/identify/{PROJECT}?api-key={API_KEY}"
        else:
            url = f"https://my-api.plantnet.org/v2/diseases/identify?api-key={API_KEY}"

        content_type = file.content_type or "image/jpeg"

        with open(file_path, "rb") as img:
            files = {
                "images": (os.path.basename(file_path), img, content_type)
            }

            data = {
                "organs": "auto"
            }

            response = requests.post(
                url,
                files=files,
                data=data,
                timeout=60
            )

        if response.status_code != 200:
            return {
                "status": "error",
                "mode": mode,
                "status_code": response.status_code,
                "raw_response": response.text
            }

        payload = response.json()
        results = payload.get("results", [])

        if not results:
            return {
                "status": "ok",
                "mode": mode,
                "commentary": "Nincs találat.",
                "context_flags": {
                    "no_result": True
                },
                "raw": payload
            }

        top = results[0]
        score = top.get("score", 0)

        if mode == "weed":
            species = top.get("species", {})
            species_name = species.get("scientificNameWithoutAuthor", "Ismeretlen")

            return {
                "status": "ok",
                "mode": mode,
                "species": species_name,
                "score": score,
                "commentary": f"Felismert növény: {species_name} ({round(score * 100)}%)",
                "context_flags": {},
                "raw": payload
            }

        label = top.get("description") or top.get("name") or "Ismeretlen"

        if mode == "disease":
            commentary = f"Lehetséges betegség: {label} ({round(score * 100)}%)"
        else:
            commentary = f"Lehetséges kártevő: {label} ({round(score * 100)}%)"

        return {
            "status": "ok",
            "mode": mode,
            "label": label,
            "score": score,
            "commentary": commentary,
            "context_flags": {},
            "raw": payload
        }

    except requests.Timeout:
        raise HTTPException(status_code=504, detail="PlantNet időtúllépés.")
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"PlantNet kérési hiba: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Belső hiba: {str(e)}")
    finally:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass
