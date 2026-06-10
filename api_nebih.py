from fastapi import FastAPI

from nebih_api import router as nebih_router


app = FastAPI(
    title="NEBIH SQL API",
    description="Read-only NEBIH product and permit lookup service.",
    version="1.0.0",
)
app.include_router(nebih_router)


@app.get("/health", operation_id="nebihHealth")
def health() -> dict[str, str]:
    return {"status": "ok"}
