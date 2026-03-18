from fastapi import FastAPI, UploadFile, File, Form
from typing import Optional
import requests
import shutil
import os
import uuid

app = FastAPI()


PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY")


def save_temp_file(upload: UploadFile):
    temp_name = f"/tmp/{uuid.uuid4()}.jpg"
    with open(temp_name, "wb") as buffer:
        shutil.copyfileobj(upload.file, buffer)
    return temp_name


def plantnet_call(path, mode="weed"):

    url = "https://my-api.plantnet.org/v2/identify/all"

    files = {
        "images": open(path, "rb")
    }

    params = {
        "api-key": PLANTNET_API_KEY,
        "lang": "en",
        "type": "kt",
    }

    r = requests.post(url, files=files, params=params)

    return r.json()


@app.post("/analyze")
async def analyze(
    file: Optional[UploadFile] = File(None),
    mode: Optional[str] = Form("weed"),
    image_path: Optional[str] = Form(None),
    file_id: Optional[str] = Form(None),
):

    temp_path = None

    # 1️⃣ normál file upload
    if file:
        temp_path = save_temp_file(file)

    # 2️⃣ GPT image_path
    elif image_path:
        temp_path = image_path

    # 3️⃣ GPT file_id (nem használjuk, fallback)
    elif file_id:
        return {
            "status": "error",
            "message": "file_id not supported"
        }

    else:
        return {
            "status": "error",
            "message": "no image received"
        }

    result = plantnet_call(temp_path, mode)

    return {
        "status": "ok",
        "mode": mode,
        "raw": result
    }
