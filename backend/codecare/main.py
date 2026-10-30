from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from codecare.config import get_settings
from codecare.csrf import CSRFMiddleware
from codecare.db import Base, engine
from codecare.logging_config import configure_logging
from codecare.production import validate_settings
from codecare.rate_limit import PathRateLimit
from codecare.routers import auth, billing, contact, github, maintenance, org, workspace
from codecare.services.email import delivery_mode
from codecare.static_site import file_response
from codecare.worker import worker_loop

settings = get_settings()
configure_logging(settings.app_env)
log = logging.getLogger("codecare")


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if settings.app_env == "production":
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_settings(settings)
    if settings.app_env != "production":
        Base.metadata.create_all(bind=engine)
    disable_worker = settings.app_env == "test" or settings.disable_worker or os.getenv("CODECARE_DISABLE_WORKER") == "1"
    task = None if disable_worker else asyncio.create_task(worker_loop())
    yield
    if task:
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=10)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass


app = FastAPI(title="CodeCare", version="0.2.0", lifespan=lifespan)
app.add_middleware(SecurityHeaders)
app.add_middleware(PathRateLimit)
app.add_middleware(CSRFMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "Stripe-Signature", "X-Hub-Signature-256", "X-CSRF-Token"],
)

app.include_router(auth.router)
app.include_router(contact.router)
app.include_router(github.router)
app.include_router(workspace.router)
app.include_router(maintenance.router)
app.include_router(billing.router)
app.include_router(org.router)


@app.get("/health")
def health():
    return {
        "ok": True,
        "github_configured": settings.github_configured,
        "github_oauth_configured": settings.github_oauth_configured,
        "stripe_configured": settings.stripe_configured,
        "supabase_configured": settings.supabase_configured,
        "email": delivery_mode(settings),
        "ai": "remote" if (settings.groq_api_key or settings.openai_api_key or settings.anthropic_api_key) else "heuristic",
    }


@app.get("/readyz")
def readyz():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        log.exception("Readiness check failed")
        return JSONResponse({"ok": False, "database": "unavailable"}, status_code=503)
    return {"ok": True, "database": "ok"}


@app.get("/")
def root():
    return file_response("index.html")


@app.get("/{full_path:path}")
def public_site(full_path: str, request: Request):
    if full_path.startswith("api/") or full_path in {"health", "readyz"}:
        raise HTTPException(status_code=404, detail="Not found")
    return file_response(full_path)
