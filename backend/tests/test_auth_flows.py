import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from codecare.services.email import OUTBOX
from tests.conftest import register


def _token_from_outbox() -> str:
    assert OUTBOX, "expected an email to be recorded"
    match = re.search(r"token=([A-Za-z0-9_\-]+)", OUTBOX[-1]["text"])
    assert match, OUTBOX[-1]["text"]
    return match.group(1)


def test_session_cookie_is_httponly(client):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "cookie@example.com", "password": "correct-horse-battery", "full_name": "Cookie"},
    )
    assert response.status_code == 200
    cookies = response.headers.get_list("set-cookie")
    session = next(item for item in cookies if item.startswith("codecare_session="))
    assert "httponly" in session.lower()
    assert "samesite=lax" in session.lower()
    me = client.get("/api/v1/auth/me")
    assert me.json()["email_verified"] is False
    assert me.json()["role"] == "owner"


def test_csrf_blocks_login_without_token(client):
    register(client)
    from fastapi.testclient import TestClient

    from codecare.main import app

    raw = TestClient(app)
    denied = raw.post("/api/v1/auth/login", json={"email": "jamie@example.com", "password": "correct-horse-battery"})
    assert denied.status_code == 403


def test_logout_revokes_session(client):
    register(client)
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
    again = client.post("/api/v1/auth/login", json={"email": "jamie@example.com", "password": "correct-horse-battery"})
    assert again.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 200


def test_forgot_reset_and_verify_email(client):
    register(client)
    verify_token = _token_from_outbox()
    verified = client.post("/api/v1/auth/verify-email", json={"token": verify_token})
    assert verified.status_code == 200
    assert client.get("/api/v1/auth/me").json()["email_verified"] is True
    reused = client.post("/api/v1/auth/verify-email", json={"token": verify_token})
    assert reused.status_code == 400

    forgot = client.post("/api/v1/auth/forgot-password", json={"email": "jamie@example.com"})
    assert forgot.status_code == 200
    assert forgot.json()["delivery"] == "console"
    hidden = client.post("/api/v1/auth/forgot-password", json={"email": "missing@example.com"})
    assert hidden.status_code == 200
    assert hidden.json()["delivery"] == forgot.json()["delivery"]
    assert "If an account exists" in hidden.json()["message"]

    reset = client.post(
        "/api/v1/auth/reset-password",
        json={"token": _token_from_outbox(), "password": "a-brand-new-secret"},
    )
    assert reset.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
    old = client.post("/api/v1/auth/login", json={"email": "jamie@example.com", "password": "correct-horse-battery"})
    assert old.status_code == 401
    new = client.post("/api/v1/auth/login", json={"email": "jamie@example.com", "password": "a-brand-new-secret"})
    assert new.status_code == 200


def test_change_password_and_delete_account(client):
    register(client)
    changed = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "correct-horse-battery", "new_password": "another-strong-secret"},
    )
    assert changed.status_code == 200
    bad = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "nope-nope-nope", "new_password": "another-strong-secret"},
    )
    assert bad.status_code == 400
    removed = client.post("/api/v1/auth/delete-account", json={"password": "another-strong-secret", "confirmation": "DELETE"})
    assert removed.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
    login = client.post("/api/v1/auth/login", json={"email": "jamie@example.com", "password": "another-strong-secret"})
    assert login.status_code == 401


def test_refresh_rotates_session(client):
    register(client)
    first = client.cookies.get("codecare_session")
    refreshed = client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200
    second = client.cookies.get("codecare_session")
    assert second and second != first
    assert client.get("/api/v1/auth/me").status_code == 200


def test_expired_session_rejected(client):
    register(client)
    from codecare.db import SessionLocal
    from codecare.models import AuthSession

    db = SessionLocal()
    row = db.query(AuthSession).one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    db.commit()
    db.close()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_member_cannot_mutate_billing_or_github(client):
    register(client, "owner@example.com")
    from codecare.db import SessionLocal
    from codecare.models import OrganizationMember

    db = SessionLocal()
    member = db.query(OrganizationMember).one()
    member.role = "member"
    db.commit()
    db.close()
    assert client.post("/api/v1/billing/checkout", json={"plan": "starter"}).status_code == 403
    assert client.post("/api/v1/github/repos/connect", json={"github_repo_ids": ["1"]}).status_code == 403
    assert client.get("/api/v1/workspace/overview").status_code == 200


def test_invite_existing_member_and_role_change(client):
    register(client, "owner@example.com")
    owner = client.get("/api/v1/auth/me").json()
    client.post("/api/v1/auth/logout")
    register(client, "member@example.com")
    client.post("/api/v1/auth/logout")
    client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": "correct-horse-battery"})
    added = client.post("/api/v1/org/members", json={"email": "member@example.com", "role": "member"})
    assert added.status_code == 200
    assert added.json()["joined"] is True
    members = client.get("/api/v1/org/members").json()
    target = next(item for item in members if item["email"] == "member@example.com")
    promoted = client.patch(f"/api/v1/org/members/{target['id']}", json={"role": "admin"})
    assert promoted.status_code == 200
    client.post("/api/v1/auth/logout")
    client.post("/api/v1/auth/login", json={"email": "member@example.com", "password": "correct-horse-battery"})
    switched = client.post("/api/v1/auth/switch-organization", json={"organization_id": owner["organization"]["id"]})
    assert switched.status_code == 200
    assert client.get("/api/v1/auth/me").json()["role"] == "admin"


def test_email_invite_for_unknown_address(client):
    register(client)
    invited = client.post("/api/v1/org/members", json={"email": "newhire@example.com", "role": "member"})
    assert invited.status_code == 200
    assert invited.json()["joined"] is False
    assert invited.json()["delivery"] == "console"
    assert "token=" in invited.json()["invite_url"]


def test_github_sign_in_requires_configuration(client):
    response = client.get("/api/v1/auth/github/start")
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"].lower()


def test_github_sign_in_creates_account(client, monkeypatch):
    from codecare.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "github_app_client_id", "client-id")
    monkeypatch.setattr(settings, "github_app_client_secret", "client-secret")

    async def exchange(code, settings=None, redirect_uri=None):
        assert code == "good-code"
        assert redirect_uri and redirect_uri.endswith("/api/v1/github/oauth/callback")
        return {"access_token": "gho_test"}

    async def user(token):
        return {"id": 42, "login": "octocat", "name": "Octo Cat"}

    async def emails(token):
        return [{"email": "octo@example.com", "primary": True, "verified": True}]

    monkeypatch.setattr("codecare.services.github.exchange_oauth_code", exchange)
    monkeypatch.setattr("codecare.services.github_login.gh.github_user", user)
    monkeypatch.setattr("codecare.services.github_login.gh.github_emails", emails)

    start = client.get("/api/v1/auth/github/start")
    assert start.status_code == 200
    state = parse_qs(urlparse(start.json()["url"]).query)["state"][0]
    callback = client.get(f"/api/v1/github/oauth/callback?code=good-code&state={state}", follow_redirects=False)
    assert callback.status_code == 303
    assert callback.headers["location"].endswith("/workspace/")
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "octo@example.com"
    assert body["email_verified"] is True
    assert body["role"] == "owner"


def test_resend_verification_skips_when_verified(client):
    register(client)
    client.post("/api/v1/auth/verify-email", json={"token": _token_from_outbox()})
    resent = client.post("/api/v1/auth/resend-verification")
    assert resent.status_code == 200
    assert resent.json()["delivery"] == "skipped"


def test_auth_routes_are_rate_limited(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from codecare.config import get_settings
    from codecare.rate_limit import PathRateLimit

    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "development")
    mini = FastAPI()
    mini.add_middleware(PathRateLimit)

    @mini.post("/api/v1/auth/login")
    def login():
        return {"ok": True}

    with TestClient(mini) as raw:
        for _ in range(10):
            assert raw.post("/api/v1/auth/login").status_code == 200
        blocked = raw.post("/api/v1/auth/login")
    assert blocked.status_code == 429


def test_unconfigured_email_does_not_claim_delivery(client, monkeypatch):
    from codecare.config import get_settings

    settings = get_settings()
    client.get("/api/v1/auth/csrf")
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "smtp_host", "")
    forgot = client.post("/api/v1/auth/forgot-password", json={"email": "jamie@example.com"})
    assert forgot.status_code == 200
    assert forgot.json()["delivery"] == "unavailable"
    assert "no message was sent" in forgot.json()["message"]
