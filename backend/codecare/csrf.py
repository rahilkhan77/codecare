from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from codecare.config import get_settings

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
EXEMPT_PREFIXES = ("/api/v1/webhooks/",)


def tokens_match(left: str, right: str) -> bool:
    if not left or not right:
        return False
    try:
        return hmac.compare_digest(left, right)
    except ValueError:
        return False


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in SAFE_METHODS or request.url.path.startswith(EXEMPT_PREFIXES):
            return await call_next(request)
        settings = get_settings()
        cookie = request.cookies.get(settings.csrf_cookie_name, "")
        header = request.headers.get("x-csrf-token", "")
        if not tokens_match(cookie, header):
            return JSONResponse({"detail": "CSRF token missing or invalid"}, status_code=403)
        return await call_next(request)
