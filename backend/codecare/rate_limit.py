from __future__ import annotations

import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from codecare.config import get_settings

# path, max_requests, window_seconds, methods
PATH_LIMITS = (
    ("/api/v1/auth/register", 5, 60, {"POST"}),
    ("/api/v1/auth/login", 10, 60, {"POST"}),
    ("/api/v1/auth/forgot-password", 5, 60, {"POST"}),
    ("/api/v1/auth/reset-password", 8, 60, {"POST"}),
    ("/api/v1/auth/verify-email", 10, 60, {"POST"}),
    ("/api/v1/auth/resend-verification", 3, 60, {"POST"}),
    ("/api/v1/auth/github/start", 10, 60, {"GET"}),
    ("/api/v1/contact", 8, 60, {"POST"}),
    ("/api/v1/webhooks/github", 60, 60, {"POST"}),
    ("/api/v1/webhooks/dodo", 60, 60, {"POST"}),
    ("/api/v1/billing/checkout", 10, 60, {"POST"}),
    ("/api/v1/billing/change-plan", 10, 60, {"POST"}),
    ("/api/v1/billing/portal", 10, 60, {"POST"}),
    ("/api/v1/billing/cancel", 10, 60, {"POST"}),
    ("/api/v1/repos/", 20, 60, {"POST"}),
)


class PathRateLimit(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _limit_for(self, path: str, method: str) -> tuple[int, int] | None:
        for prefix, count, window, methods in PATH_LIMITS:
            if method not in methods:
                continue
            if prefix.endswith("/") and path.startswith(prefix) and path.endswith("/scans"):
                return count, window
            if path == prefix:
                return count, window
        return None

    async def dispatch(self, request: Request, call_next):
        if get_settings().app_env == "test":
            return await call_next(request)
        limit = self._limit_for(request.url.path, request.method)
        if not limit:
            return await call_next(request)
        count, window = limit
        key = f"{request.client.host if request.client else 'unknown'}:{request.url.path}"
        now = time.time()
        bucket = self._hits[key]
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= count:
            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
        bucket.append(now)
        return await call_next(request)
