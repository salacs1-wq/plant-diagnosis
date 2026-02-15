import os
import io
import base64
import mimetypes
import requests
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl

app = FastAPI()

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_PROJECT = os.getenv("PLANTNET_PROJECT", "all").strip()  # pl. "all" vagy "weurope"
PLANTNET_ENDPOINT = f"https://my-api.plantnet.org/v2/identify/{PLANTNET_PROJECT}"

if not PLANTNET_API_KEY:
    print("WARNING: PLANTNET_API_KEY is missing!")

class IdentifyUrlRequest(BaseModel):
    image_url: HttpUrl
    organs: str = "leaf"

def call_plantnet(image_bytes: bytes, filename: str, organs: str):
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="PLANTNET_API_KEY missing on server")

    # content-type tippelés
    ctype, _ = mimetypes.guess_type(filename)
    if not ctype:
        ctype = "image/jpeg"

    files = {
        "images": (filename, image_bytes, ctype)
    }
    data = {
        "organs": organs
    }
    params = {
        "api-key": PLANTNET_API_KEY
    }

    r = requests.post(PLANTNET_ENDPOINT, params=params, files=files, data=data, timeout=60)
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)

    return r.json()

@app.get("/")
def health():
    return {"status": "ok", "message": "Plant diagnosis API is running"}

# 1) Hoppscotch / klasszikus multipart (ez nálad már működik)
@app.post("/identify")
async def identify_plant(image: UploadFile = File(...), organs: str = Form("leaf")):
    img_bytes = await image.read()
    result = call_plantnet(img_bytes, image.filename or "image.jpg", organs)
    return JSONResponse(result)

# 2) GPT Actions / JSON + publikus URL (ez kell a GPT-hez)
@app.post("/identify_url")
def identify_plant_by_url(payload: IdentifyUrlRequest):
    try:
        img_resp = requests.get(str(payload.image_url), timeout=30)
        img_resp.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not download image_url: {e}")

    # fájlnév próbálgatás
    filename = os.path.basename(str(payload.image_url)) or "image.jpg"
    img_bytes = img_resp.content

    result = call_plantnet(img_bytes, filename, payload.organs)
    return JSONResponse(result)
