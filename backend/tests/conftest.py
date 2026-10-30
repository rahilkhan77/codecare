from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["APP_SECRET"] = "test-secret-key-please-change"
os.environ["GITHUB_WEBHOOK_SECRET"] = "webhook-secret"
os.environ["CREDENTIALS_ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

from collections.abc import Generator  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from codecare.db import Base, engine  # noqa: E402
from codecare.main import app  # noqa: E402
from codecare.services.email import reset_outbox  # noqa: E402

CSRF_EXEMPT_PREFIXES = ("/api/v1/webhooks/",)


class CSRFClient(TestClient):
    def request(self, method, url, **kwargs):
        method_name = method.upper()
        path = url.split("?", 1)[0]
        if method_name not in {"GET", "HEAD", "OPTIONS"} and not path.startswith(CSRF_EXEMPT_PREFIXES):
            if "codecare_csrf" not in self.cookies:
                super().request("GET", "/api/v1/auth/csrf")
            token = self.cookies.get("codecare_csrf")
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault("X-CSRF-Token", token or "")
            kwargs["headers"] = headers
        return super().request(method, url, **kwargs)


@pytest.fixture
def client() -> Generator[CSRFClient, None, None]:
    reset_outbox()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with CSRFClient(app) as test_client:
        yield test_client
    reset_outbox()


def register(client: TestClient, email: str = "jamie@example.com") -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery", "full_name": "Jamie Example"},
    )
    assert response.status_code == 200, response.text
    return response.json()
