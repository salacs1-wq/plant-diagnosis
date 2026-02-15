import os
import mimetypes
import requests
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI()

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", "").strip()
PLANTNET_URL = "https://my-api.plantnet.org/v2/identify/all"

@app.get("/")
def health():
    return {"status": "ok", "message": "plant-diagnosis running"}

def _guess_content_type(filename: str) -> str:
    ct, _ = mimetypes.guess_type(filename or "")
    return ct or "application/octet-stream"

@app.post("/identify")
async def identify(
    # A GPT action jellemzően "image"-et küld, Hoppscotch sokszor szintén.
    image: UploadFile | None = File(default=None),
    # Biztonság kedvéért: ha valaki "images" néven küldi (többes), azt is fogadjuk.
    images: UploadFile | None = File(default=None),
    organs: str = Form("leaf"),
):
    if not PLANTNET_API_KEY:
        raise HTTPException(status_code=500, detail="Missing PLANTNET_API_KEY env var")

    file_obj = image or images
    if file_obj is None:
        # Ne hívjuk a PlantNet-et kép nélkül, mert úgyis 400-at ad.
        raise HTTPException(status_code=400, detail="Missing file field: image")

    content = await file_obj.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty image file")

    ct = file_obj.content_type or _guess_content_type(file_obj.filename)

    # PlantNet felé KÖTELEZŐEN 'images' néven küldjük (többes!)
    files = {
        "images": (file_obj.filename or "image.jpg", content, ct)
    }
    data = {
        "organs": organs or "leaf"
    }

    url = f"{PLANTNET_URL}?api-key={PLANTNET_API_KEY}"

    try:
        r = requests.post(url, files=files, data=data, timeout=60)
        # Ha PlantNet hibázik, add vissza a teljes body-t (sokat segít debuggolni)
        if r.status_code >= 400:
            return JSONResponse(
                status_code=r.status_code,
                content={
                    "plantnet_status": r.status_code,
                    "plantnet_error": r.text,
                },
            )
        return r.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"PlantNet request failed: {str(e)}")
