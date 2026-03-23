from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import requests
import os
from openai import OpenAI

app = FastAPI()


# =========================
# API KEYS
# =========================

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
# SYSTEM PROMPT
# =========================

SYSTEM_PROMPT = """
Te egy növényorvosi diagnosztikai rendszer vagy.

SZABÁLYOK:

1. Diagnózis csak backend API alapján adható.
2. Ha nincs PlantNet vagy iNat eredmény → nem adhatsz diagnózist.
3. Nem tippelhetsz.
4. Nem használhatsz morfológiai feltételezést API nélkül.
5. Dr. Keszthelyi metodika csak ellenőrzésre használható.
6. Válasz JSON legyen.

Kimenet:

{
  "szerv": "",
  "mechanizmus": "",
  "kartep": "",
  "karosito_kategoria": "",
  "karosito_pontositas": "",
  "bizonyossag": "",
  "indoklas": ""
}
"""


# =========================
# FILE DOWNLOAD
# =========================

def get_download_link(file_refs):
    return file_refs[0].download_link


def download_image(url):
    r = requests.get(url)
    return r.content


# =========================
# PLANTNET
# =========================

def call_plantnet(image_bytes):

    url = "https://my-api.plantnet.org/v2/identify/all"

    params = {
        "api-key": PLANTNET_API_KEY
    }

    files = {
        "images": ("image.jpg", image_bytes, "image/jpeg")
    }

    r = requests.post(url, params=params, files=files)

    return r.json()


# =========================
# INAT
# =========================

def call_inat(image_bytes):

    url = "https://api.inaturalist.org/v1/computervision/score_image"

    try:

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
            "plant": plant,
            "insect": insect
        }

    except Exception as e:

        return {
            "error": str(e)
        }


# =========================
# GPT
# =========================

def call_gpt(payload):

    if not OPENAI_API_KEY:

        return {
            "error": "no openai key"
        }

    response = client.chat.completions.create(

        model="gpt-4o-mini",

        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": str(payload)
            }
        ],

        temperature=0
    )

    return response.choices[0].message.content


# =========================
# ROUTE
# =========================

@app.post("/diagnose-dp")
async def diagnose(req: DiagnoseRequest):

    try:

        image_url = get_download_link(req.openaiFileIdRefs)

        image_bytes = download_image(image_url)

        plantnet = call_plantnet(image_bytes)

        inat = call_inat(image_bytes)

        # HA nincs plantnet → nincs diagnózis

        if not plantnet or "results" not in plantnet:

            return {
                "status": "error",
                "message": "no plantnet result",
                "plantnet": plantnet,
                "inat": inat
            }

        gpt_input = {
            "mode": req.caseType,
            "plantnet": plantnet,
            "inat": inat
        }

        gpt_result = call_gpt(gpt_input)

        return {
            "status": "ok",
            "plantnet": plantnet,
            "inat": inat,
            "gpt": gpt_result
        }

    except Exception as e:

        return {
            "status": "error",
            "message": str(e)
        }


# =========================
# DEBUG
# =========================

@app.get("/debug-env")
def debug_env():

    plantnet_key = os.getenv("PLANTNET_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    return {
        "plantnet_key_present": bool(plantnet_key),
        "openai_key_present": bool(openai_key)
    }


@app.get("/")
def root():
    return {"status": "ok", "version": "stable-v2"}
