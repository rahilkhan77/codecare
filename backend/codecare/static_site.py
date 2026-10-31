from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from codecare.config import FRONTEND_ROOT

ALLOWED_ROOT = {
    "index.html",
    "styles.css",
    "theme.css",
    "pages.css",
    "app.js",
    "favicon.svg",
    "og-image.svg",
    "robots.txt",
    "sitemap.xml",
}

ALLOWED_DIRS = {
    "pricing",
    "about",
    "contact",
    "how-it-works",
    "privacy-policy",
    "terms",
    "cookie-policy",
    "acceptable-use",
    "workspace",
}


def safe_file(full_path: str) -> Path:
    cleaned = full_path.strip("/")
    if not cleaned:
        return FRONTEND_ROOT / "index.html"
    candidate = (FRONTEND_ROOT / cleaned).resolve()
    if FRONTEND_ROOT.resolve() not in candidate.parents and candidate != FRONTEND_ROOT.resolve():
        raise HTTPException(status_code=404, detail="Not found")
    relative = candidate.relative_to(FRONTEND_ROOT.resolve()).as_posix()
    if relative in ALLOWED_ROOT:
        return candidate
    top = relative.split("/", 1)[0]
    if top in ALLOWED_DIRS and candidate.is_file():
        return candidate
    if top in ALLOWED_DIRS and candidate.is_dir():
        index = candidate / "index.html"
        if index.is_file():
            return index
    raise HTTPException(status_code=404, detail="Not found")


def file_response(full_path: str) -> FileResponse:
    path = safe_file(full_path)
    cache = "public, max-age=86400" if path.suffix in {".css", ".js", ".svg", ".png", ".webp", ".ico"} else "no-cache"
    return FileResponse(path, headers={"Cache-Control": cache})
