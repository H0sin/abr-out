from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import admin, configs, listings, me, prober, webhook, withdrawals
from app.common.logging import setup_logging

setup_logging()

app = FastAPI(title="abr-out API", version="0.1.0")

# Allow the Telegram Mini App webview to call our API.
# Telegram serves WebApps from t.me / web.telegram.org but the iframe origin
# can vary; we restrict to https only and allow same-origin (self-hosted Mini App).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(prober.router)
app.include_router(webhook.router)
app.include_router(admin.router)
app.include_router(me.router)
app.include_router(listings.router)
app.include_router(configs.router)
app.include_router(withdrawals.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# Serve the Telegram Mini App (React SPA) under /app
# The frontend is built into /app/static during the Docker image build.
_static_dir = Path(os.environ.get("STATIC_DIR", "/app/static"))
if _static_dir.is_dir():
    @app.get("/app", include_in_schema=False)
    async def _app_redirect() -> RedirectResponse:
        return RedirectResponse(url="/app/")

    app.mount("/app", StaticFiles(directory=str(_static_dir), html=True), name="webapp")
