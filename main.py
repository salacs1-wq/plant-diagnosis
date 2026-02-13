import os
import requests
from fastapi import FastAPI, File, UploadFile, HTTPException

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_BASE = "https://my-api.plantnet.org/v2/identify"

app = FastAPI()

@app.get("/")
def home():
    return {"status": "ok", "message": "Plant diagnosis API running"}

@app.post("/identify")
async def identify(image: UploadFile = File(...)):
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="API key not set")

    image_bytes = await image.read()

    url = f"{PLANTNET_BASE}/all"
    params = {"api-key": PLANTNET_API_KEY}
    files = {"images": ("image.jpg", image_bytes, "image/jpeg")}
    data = {"organs": "leaf"}

    r = requests.post(url, params=params, files=files, data=data)

    if r.status_code != 200:
        raise HTTPException(status_code=500, detail=r.text)

    return r.json()
