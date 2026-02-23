import os
import hashlib
import httpx
from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI()

PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY")
PLANTNET_BASE = "https://my-api.plantnet.org/v2"

# -----------------------------
# MODELS
# -----------------------------

class OpenAIFileRef(BaseModel):
    name: str
    id: str
    mime_type: str
    download_link: str

class DiagnoseFilesRequest(BaseModel):
    openaiFileIdRefs: List[OpenAIFileRef]
    project: Optional[str] = "k-middle-europe"
    mode: Optional[str] = "learning"
    caseType: Optional[str] = "weed"

# -----------------------------
# UTILS
# -----------------------------

async def download_image(url: str):
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content

async def plant_identify(images: List[bytes], project: str):
    url = f"{PLANTNET_BASE}/identify/{project}"
    files = [("images", img) for img in images]
    params = {"api-key": PLANTNET_API_KEY}

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, params=params, files=files)
        r.raise_for_status()
        return r.json()

async def plant_diseases(images: List[bytes], project: str):
    url = f"{PLANTNET_BASE}/identify/{project}"
    files = [("images", img) for img in images]
    params = {
        "api-key": PLANTNET_API_KEY,
        "include-related-images": "false",
        "diseases": "true"
    }

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, params=params, files=files)
        r.raise_for_status()
        return r.json()

def extract_top_matches(data):
    results = data.get("results", [])
    top = []
    for r in results[:5]:
        top.append({
            "name": r["species"]["scientificNameWithoutAuthor"],
            "score": r["score"]
        })
    return top

# -----------------------------
# MAIN ENDPOINT
# -----------------------------

@app.post("/diagnose_files")
async def diagnose_files(req: DiagnoseFilesRequest):

    images = []
    for f in req.openaiFileIdRefs:
        img = await download_image(f.download_link)
        images.append(img)

    case = req.caseType or "weed"
    project = req.project or "k-middle-europe"

    plant_data = None
    disease_data = None

    # -----------------------------
    # CASE LOGIC
    # -----------------------------

    if case == "weed":
        # ONLY PLANT IDENTIFY
        plant_data = await plant_identify(images, project)

    elif case == "disease":
        # DISEASE + BACKGROUND PLANT
        disease_data = await plant_diseases(images, project)
        plant_data = await plant_identify(images, project)

    elif case == "pest":
        # TRY DISEASE MODEL AS PEST SIGNAL
        disease_data = await plant_diseases(images, project)
        plant_data = await plant_identify(images, project)

    else:
        raise HTTPException(status_code=400, detail="Invalid caseType")

    # -----------------------------
    # RESPONSE BUILD
    # -----------------------------

    response = {
        "plant": None,
        "issue": None,
        "summary": {}
    }

    if plant_data:
        plant_top = extract_top_matches(plant_data)
        response["plant"] = plant_top

        if plant_top:
            response["summary"]["bestPlant"] = plant_top[0]["name"]
            response["summary"]["plantScore"] = plant_top[0]["score"]

    if disease_data and case in ["disease", "pest"]:
        issue_top = extract_top_matches(disease_data)
        response["issue"] = issue_top

        if issue_top:
            response["summary"]["bestIssue"] = issue_top[0]["name"]
            response["summary"]["issueScore"] = issue_top[0]["score"]

    response["summary"]["caseType"] = case

    return response
