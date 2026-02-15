from fastapi import FastAPI, UploadFile, File, Form
import requests
import os

app = FastAPI()

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY")

@app.get("/")
def health():
    return {"status": "ok", "message": "Plant diagnosis API running"}

@app.post("/identify")
async def identify(
    image: UploadFile = File(...),
    organs: str = Form("leaf")
):
    try:
        files = {
            "images": (image.filename, await image.read(), image.content_type)
        }

        data = {
            "organs": organs
        }

        url = f"https://my-api.plantnet.org/v2/identify/all?api-key={PLANTNET_API_KEY}"

        response = requests.post(url, files=files, data=data)

        return response.json()

    except Exception as e:
        return {"error": str(e)}
