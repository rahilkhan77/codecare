import json
from unittest.mock import AsyncMock

from codecare.services.ai import HeuristicProvider, diagnose, propose_fix
from codecare.services.demo import DEMO_EMAIL, DEMO_PASSWORD, seed_demo
from codecare.services.email import OUTBOX, OutboundEmail, deliver


class FakeAI:
    name = "fake"

    async def complete(self, system: str, user: str) -> str:
        return json.dumps(
            {
                "summary": "[Sample] Proposed pin. This fake provider did not call a network.",
                "files": [{"path": "requirements.txt", "content": "requests==2.32.3\n"}],
                "root_cause": "[Sample] Outdated pin",
                "impact": "review only",
                "confidence": "low",
                "recommended_tests": ["pytest"],
                "caution": "not verified",
            }
        )


def test_fake_ai_proposes_without_network(monkeypatch):
    monkeypatch.setattr("codecare.services.ai.get_provider", lambda: FakeAI())

    async def run():
        name, _raw, remote = await diagnose("title", "evidence", "context")
        assert name == "fake"
        assert remote is True
        provider, summary, files, generated = await propose_fix("title", "evidence", {"requirements.txt": "requests==1.0.0\n"})
        return provider, summary, files, generated

    import asyncio

    provider, summary, files, generated = asyncio.run(run())
    assert provider == "fake"
    assert generated is True
    assert "requests==2.32.3" in files["requirements.txt"]
    assert "[Sample]" in summary


def test_heuristic_still_refuses_to_invent_a_patch():
    async def run():
        return await propose_fix("title", "evidence", {"app.py": "print('ok')\n"})

    import asyncio

    name, summary, files, generated = asyncio.run(run())
    assert isinstance(HeuristicProvider(), HeuristicProvider)
    assert name == "heuristic"
    assert files == {}
    assert generated is False
    assert "did not generate a patch" in summary


def test_fake_smtp_records_message_without_a_server():
    before = len(OUTBOX)
    mode = deliver(OutboundEmail(to="review@codecare.si", subject="[Sample] Hello", text="console only"))
    assert mode == "console"
    assert OUTBOX[-1]["to"] == "review@codecare.si"
    assert len(OUTBOX) == before + 1


def test_mocked_dodo_checkout_returns_hosted_url(client, monkeypatch):
    from codecare.config import get_settings
    from tests.conftest import register

    register(client, "billing@example.com")
    settings = get_settings()
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "dodo_payments_api_key", "dodo_test_key")
    monkeypatch.setattr(settings, "dodo_payments_environment", "test_mode")
    monkeypatch.setattr(settings, "dodo_product_starter", "pdt_starter")

    def fake_create(**kwargs):
        return {"checkout_url": "https://checkout.dodopayments.com/session/chs_sample", "session_id": "chs_sample"}

    monkeypatch.setattr("codecare.routers.billing.dodo.create_checkout", fake_create)
    response = client.post("/api/v1/billing/checkout", json={"plan": "starter"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["url"].startswith("https://checkout.dodopayments.com/")
    assert body["session_id"] == "chs_sample"
    assert "dodo_test_key" not in response.text


def test_fake_github_lists_only_mocked_repos(client, monkeypatch):
    from codecare.db import SessionLocal
    from codecare.models import GithubInstallation, OrganizationMember, new_id
    from tests.conftest import register

    register(client, "gh@example.com")
    db = SessionLocal()
    member = db.query(OrganizationMember).one()
    db.add(GithubInstallation(id=new_id(), organization_id=member.organization_id, installation_id="555"))
    db.commit()
    db.close()
    monkeypatch.setattr("codecare.routers.github.gh.installation_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(
        "codecare.routers.github.gh.list_installation_repos",
        AsyncMock(return_value=[{"id": 7, "full_name": "sample/web", "private": True, "default_branch": "main"}]),
    )
    response = client.get("/api/v1/github/repos/available")
    assert response.status_code == 200
    assert response.json()[0]["full_name"] == "sample/web"


def test_demo_seed_is_labeled_and_idempotent(client):
    from codecare.db import SessionLocal

    db = SessionLocal()
    seed_demo(db)
    seed_demo(db)
    db.close()
    login = client.post("/api/v1/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
    assert login.status_code == 200
    issues = client.get("/api/v1/issues").json()
    assert issues and all("[Sample]" in item["title"] for item in issues)
    pulls = client.get("/api/v1/pull-requests").json()
    assert pulls[0]["title"].startswith("[Sample]")
    assert pulls[0]["url"].startswith("https://github.com/sample/")
    repos = client.get("/api/v1/repos").json()
    assert repos[0]["full_name"] == "sample/maintenance-fixture"
    github = client.get("/api/v1/github/status").json()
    assert github["sample"] is True
    assert github["connected"] is False
    assert client.get("/api/v1/github/repos/available").json() == []


def test_security_headers_and_static_cache(client):
    home = client.get("/")
    assert "Content-Security-Policy" in home.headers
    assert "frame-ancestors 'none'" in home.headers["Content-Security-Policy"]
    assert home.headers["X-Content-Type-Options"] == "nosniff"
    css = client.get("/styles.css")
    assert css.headers["Cache-Control"].startswith("public")
    assert client.get("/workspace/login.html").headers["Cache-Control"] == "no-cache"


def test_production_rejects_demo_mode():
    import pytest

    from codecare.production import validate_settings
    from tests.test_production import VALID_FERNET, _settings

    with pytest.raises(RuntimeError, match="DEMO_MODE"):
        validate_settings(
            _settings(
                app_env="production",
                app_secret="a" * 40,
                credentials_encryption_key=VALID_FERNET,
                database_url="postgresql+psycopg2://codecare:codecare@localhost/codecare",
                public_base_url="https://codecare.si",
                api_cors_origins="https://codecare.si",
                demo_mode=True,
            )
        )
