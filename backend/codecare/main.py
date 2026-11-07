from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import JSONResponse

from codecare.config import get_settings
from codecare.csrf import CSRFMiddleware
from codecare.db import Base, SessionLocal, engine
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

# Hash of the inline theme bootstrap shared by every HTML page.
THEME_SCRIPT_HASH = "sha256-FvTAsFHTkIMHUj7SXhVo6xJLkcvwVR92OEjuiqL3c7I="
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    f"script-src 'self' '{THEME_SCRIPT_HASH}'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com; "
    "frame-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'"
)


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        if settings.app_env == "production":
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_settings(settings)
    if settings.app_env != "production":
        Base.metadata.create_all(bind=engine)
    if settings.demo_mode:
        from codecare.services.demo import seed_demo

        db = SessionLocal()
        try:
            seed_demo(db)
        finally:
            db.close()
    disable_worker = settings.app_env == "test" or settings.disable_worker or os.getenv("CODECARE_DISABLE_WORKER") == "1"
    task = None if disable_worker else asyncio.create_task(worker_loop())
    yield
    if task:
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=10)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass


_production = settings.app_env == "production"
app = FastAPI(
    title="CodeCare",
    version="0.2.0",
    lifespan=lifespan,
    debug=False,
    docs_url=None if _production else "/docs",
    redoc_url=None if _production else "/redoc",
    openapi_url=None if _production else "/openapi.json",
)
app.add_middleware(SecurityHeaders)
app.add_middleware(PathRateLimit)
app.add_middleware(CSRFMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Hub-Signature-256", "X-CSRF-Token"],
)
app.add_middleware(GZipMiddleware, minimum_size=512)

app.include_router(auth.router)
app.include_router(contact.router)
app.include_router(github.router)
app.include_router(workspace.router)
app.include_router(maintenance.router)
app.include_router(billing.router)
app.include_router(org.router)


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    log.exception("Unhandled error on %s", request.url.path)
    if settings.app_env == "production":
        return JSONResponse(status_code=500, content={"detail": "Something went wrong."})
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


@app.get("/health")
def health():
    return {
        "ok": True,
        "github_configured": settings.github_configured,
        "github_oauth_configured": settings.github_oauth_configured,
        "dodo_configured": settings.dodo_configured,
        "supabase_configured": settings.supabase_configured,
        "email": delivery_mode(settings),
        "demo_mode": settings.demo_mode,
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
