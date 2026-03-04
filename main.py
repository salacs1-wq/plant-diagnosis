from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
from PIL import Image
import io

app = FastAPI(title="Plant Diagnosis API", version="1.0.0")

@app.get("/")
def root():
    return {"ok": True, "service": "plant-diagnosis-1", "hint": "Use /health or /docs"}

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/diagnose")
async def diagnose(
    file: UploadFile = File(...),
    caseType: str = Form("weed"),          # weed|disease|pest|symptom
    project: str = Form("k-middle-europe"),# fixen ez legyen az alap
    mode: str = Form("expert"),            # expert|fast
):
    # Minimál validálás + visszaadunk hasznos debug infót
    content = await file.read()
    try:
        img = Image.open(io.BytesIO(content))
        w, h = img.size
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "A feltöltött fájl nem olvasható képként."},
        )

    # Itt később jön a PlantNet / saját logika
    return {
        "ok": True,
        "received": {
            "filename": file.filename,
            "content_type": file.content_type,
            "bytes": len(content),
            "width": w,
            "height": h,
        },
        "params": {
            "caseType": caseType,
            "project": project,
            "mode": mode,
        },
        "result": {
            "note": "Diagnosztika stub. Ha ez megy, utána kötjük rá a tényleges API-t."
        },
    }
