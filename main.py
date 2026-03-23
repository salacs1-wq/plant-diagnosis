from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional, Any, Dict
import requests
import os
from openai import OpenAI

app = FastAPI()

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)


# =========================
# MODELS
# =========================
class OpenAIFileRef(BaseModel):
    id: str
    name: Optional[str] = None
    mime_type: Optional[str] = None
    download_link: Optional[str] = None


class DiagnoseRequest(BaseModel):
    openaiFileIdRefs: List[OpenAIFileRef]
    project: str
    mode: str
    caseType: str  # weed / disease / pest


# =========================
# VALIDATION
# =========================
def validate_request(req: DiagnoseRequest) -> None:
    if req.project != "k-middle-europe":
        raise ValueError("A project mezőnek 'k-middle-europe' értékűnek kell lennie.")

    if req.mode != "expert":
        raise ValueError("A mode mezőnek 'expert' értékűnek kell lennie.")

    if req.caseType not in ["weed", "disease", "pest"]:
        raise ValueError("A caseType csak 'weed', 'disease' vagy 'pest' lehet.")


# =========================
# FILE
# =========================
def get_download_link(file_refs):
    return file_refs[0].download_link


def download_image(download_link):
    return requests.get(download_link).content


# =========================
# PLANTNET
# =========================
def call_plantnet(image_bytes):
    url = "https://my-api.plantnet.org/v2/identify/all"
    params = {"api-key": PLANTNET_API_KEY}
    files = {"images": ("image.jpg", image_bytes, "image/jpeg")}
    return requests.post(url, params=params, files=files).json()


def call_plantnet_diseases(image_bytes):
    url = "https://my-api.plantnet.org/v2/diseases/identify"
    params = {"api-key": PLANTNET_API_KEY}
    files = {"images": ("image.jpg", image_bytes, "image/jpeg")}
    return requests.post(url, params=params, files=files).json()


# =========================
# INAT
# =========================
def call_inat(image_bytes):
    url = "https://api.inaturalist.org/v1/computervision/score_image"

    plant = requests.post(
        url,
        files={"image": ("image.jpg", image_bytes, "image/jpeg")},
        data={"taxon_id": 47126}
    ).json()

    insect = requests.post(
        url,
        files={"image": ("image.jpg", image_bytes, "image/jpeg")},
        data={"taxon_id": 47158}
    ).json()

    return {
        "plant": plant.get("results", [])[:3],
        "insect": insect.get("results", [])[:3]
    }


# =========================
# GPT
# =========================
def call_gpt(payload):
    response = client.chat.completions.create(
        model="gpt-5.3",
        messages=[
            {
                "role": "system",
                "content": "Te egy növényorvosi diagnosztikai rendszer vagy."
            },
            {
                "role": "user",
                "content": str(payload)
            }
        ]
    )

    return response.choices[0].message.content


# =========================
# ROUTE
# =========================
@app.post("/diagnose-dp")
async def diagnose(req: DiagnoseRequest):
    try:
        validate_request(req)

        image_url = get_download_link(req.openaiFileIdRefs)
        image_bytes = download_image(image_url)

        plantnet = call_plantnet(image_bytes)
        inat = call_inat(image_bytes)

        gpt_input = {
            "mode": req.caseType,
            "plantnet": plantnet,
            "inat": inat
        }

        gpt_result = call_gpt(gpt_input)

        return {
            "status": "success",
            "plantnet": plantnet,
            "inat": inat,
            "gpt": gpt_result
        }

    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }


@app.get("/")
def root():
    return {"status": "ok"}
