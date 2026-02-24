import os
import platform
import time
import uuid
from typing import Any, Dict

from fastapi import FastAPI

# -------------------------------------------------
# FIX VERZIÓ (ne env-ből jöjjön)
# -------------------------------------------------
APP_VERSION = "1.2.5"
BUILD_ID = str(uuid.uuid4())
BUILD_TIME = int(time.time())

app = FastAPI(
    title="Plant Diagnosis API",
    version=APP_VERSION,
)

# -------------------------------------------------
# ROOT
# -------------------------------------------------
@app.get("/")
async def root() -> Dict[str, str]:
    return {"status": "ok"}

# -------------------------------------------------
# HEALTH
# -------------------------------------------------
@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}

# -------------------------------------------------
# VERSION
# -------------------------------------------------
@app.get("/version")
async def version() -> Dict[str, Any]:
    return {
        "version": APP_VERSION,
        "build_id": BUILD_ID,
        "build_time": BUILD_TIME,
        "python": platform.python_version(),
    }
