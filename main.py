from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from typing import Optional
import requests
import shutil
import os
import uuid

app = FastAPI()

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "")
PLANTNET_PROJECT = "k-middle-europe"


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


def save_upload_to_temp(upload: UploadFile) -> str:
    ext = os.path.splitext(upload.filename or "")[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".webp"]:
        ext = ".jpg"

    temp_path = f"/tmp/{uuid.uuid4()}{ext}"
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(upload.file, buffer)
    return temp_path


def save_url_to_temp(url: str) -> str:
    temp_path = f"/tmp/{uuid.uuid4()}.jpg"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    with open(temp_path, "wb") as f:
        f.write(resp.content)
    return temp_path


def call_plantnet(image_path: str, mode: str) -> dict:
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="Hiányzik a PLANTNET_API_KEY environment variable.")

    if mode == "weed":
        url = f"https://my-api.plantnet.org/v2/identify/{PLANTNET_PROJECT}?api-key={PLANTNET_API_KEY}"
    else:
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
            "context_flags": {"no_result": True},
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


@app.post("/analyze")
async def analyze(
    file: Optional[UploadFile] = File(None),
    mode: Optional[str] = Form("weed"),
    image_path: Optional[str] = Form(None),
    file_id: Optional[str] = Form(None)
):
    mode = (mode or "weed").strip().lower()
    if mode not in ["weed", "disease", "pest"]:
        mode = "weed"

    temp_path = None

    try:
        # 1. Normál fájlfeltöltés
        if file is not None:
            temp_path = save_upload_to_temp(file)

        # 2. Ha a GPT/valami URL-t küld
        elif image_path:
            if image_path.startswith("http://") or image_path.startswith("https://"):
                temp_path = save_url_to_temp(image_path)

            # 3. Ha a szerveren tényleges helyi elérési út
            elif os.path.exists(image_path):
                temp_path = image_path

            else:
                return {
                    "status": "error",
                    "mode": mode,
                    "message": "Az image_path megérkezett, de nem URL és nem létező helyi fájl a backend számára.",
                    "context_flags": {
                        "image_path_unreachable": True
                    }
                }

        # 4. GPT file_id fallback
        elif file_id:
            return {
                "status": "error",
                "mode": mode,
                "message": "A file_id megérkezett, de a backend közvetlen OpenAI-fájl letöltés nincs bekötve.",
                "context_flags": {
                    "file_id_not_supported": True
                }
            }

        # 5. Semmi kép nem jött
        else:
            return {
                "status": "error",
                "mode": mode,
                "message": "No image received",
                "context_flags": {
                    "no_image": True
                }
            }

        return call_plantnet(temp_path, mode)

    except requests.Timeout:
        raise HTTPException(status_code=504, detail="PlantNet időtúllépés.")
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Hálózati hiba: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Belső hiba: {str(e)}")
    finally:
        try:
            if temp_path and temp_path.startswith("/tmp/") and os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass
