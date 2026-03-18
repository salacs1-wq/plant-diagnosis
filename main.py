from fastapi import FastAPI, File, UploadFile
import requests
import shutil
import uuid
import os

app = FastAPI()

API_KEY = "IDE_IRD_A_PLANTNET_API_KULCSOD"
PROJECT = "all"


@app.post("/analyze")
async def analyze(file: UploadFile = File(...), mode: str = "weed"):

    mode = (mode or "weed").lower().strip()

    file_id = str(uuid.uuid4())
    file_path = f"temp_{file_id}.jpg"

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:

        # ================= ROUTE =================

        if mode == "weed":
            url = f"https://my-api.plantnet.org/v2/identify/{PROJECT}?api-key={API_KEY}"

        elif mode == "disease":
            url = f"https://my-api.plantnet.org/v2/diseases/identify?api-key={API_KEY}"

        elif mode == "pest":
            url = f"https://my-api.plantnet.org/v2/diseases/identify?api-key={API_KEY}"

        else:
            mode = "weed"
            url = f"https://my-api.plantnet.org/v2/identify/{PROJECT}?api-key={API_KEY}"

        # ================= REQUEST =================

        with open(file_path, "rb") as img:

            files = {
                "images": (os.path.basename(file_path), img, "image/jpeg")
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

        # ================= ERROR =================

        if response.status_code != 200:
            return {
                "status": "error",
                "mode": mode,
                "status_code": response.status_code,
                "raw_response": response.text
            }

        data = response.json()
        results = data.get("results", [])

        if not results:
            return {
                "status": "ok",
                "mode": mode,
                "commentary": "Nincs találat",
                "context_flags": {"no_result": True},
                "raw": data
            }

        # ================= WEED =================

        if mode == "weed":

            top = results[0]

            species = top.get("species", {})
            name = species.get("scientificNameWithoutAuthor", "Ismeretlen")

            score = top.get("score", 0)

            commentary = f"Felismert növény: {name} ({round(score*100)}%)"

            return {
                "status": "ok",
                "mode": mode,
                "species": name,
                "score": score,
                "commentary": commentary,
                "context_flags": {},
                "raw": data
            }

        # ================= DISEASE / PEST =================

        top = results[0]

        label = (
            top.get("description")
            or top.get("name")
            or "Ismeretlen"
        )

        score = top.get("score", 0)

        if mode == "disease":
            commentary = f"Lehetséges betegség: {label} ({round(score*100)}%)"

        else:
            commentary = f"Lehetséges kártevő: {label} ({round(score*100)}%)"

        return {
            "status": "ok",
            "mode": mode,
            "label": label,
            "score": score,
            "commentary": commentary,
            "context_flags": {},
            "raw": data
        }

    finally:

        if os.path.exists(file_path):
            os.remove(file_path)
