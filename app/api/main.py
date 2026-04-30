from __future__ import annotations

from fastapi import FastAPI

from app.api.routes import admin, prober, webhook
from app.common.logging import setup_logging

setup_logging()

app = FastAPI(title="abr-out API", version="0.1.0")

app.include_router(prober.router)
app.include_router(webhook.router)
app.include_router(admin.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
