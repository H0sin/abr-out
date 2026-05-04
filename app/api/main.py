from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import admin, configs, listings, me, prober, webhook, withdrawals
from app.common.logging import setup_logging
from app.common.settings import get_settings

setup_logging()

app = FastAPI(title="abr-out API", version="0.1.0")

# Allow the Telegram Mini App webview to call our API.
# Telegram serves the Mini App from web.telegram.org and (on desktop/mobile)
# from telegram-internal webviews whose Origin is "null" — those requests
# don't go through CORS at all, so we don't need to allow-list them.
# Browser-based access is restricted to our own domain plus Telegram Web.
# Note: ``allow_credentials=False`` because auth uses a non-cookie header
# (``Authorization: tma <initData>``) — this also keeps a strict origin list
# meaningful (otherwise the spec would force "*" → no credentials).
_settings = get_settings()
_allowed_origins: list[str] = [
    "https://web.telegram.org",
    "https://k.web.telegram.org",
    "https://z.web.telegram.org",
    "https://a.web.telegram.org",
]
if _settings.domain:
    _allowed_origins.append(f"https://{_settings.domain.strip().strip('/')}")
if _settings.public_base_url:
    _allowed_origins.append(_settings.public_base_url)
# Deduplicate while preserving order.
_allowed_origins = list(dict.fromkeys(_allowed_origins))

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
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
