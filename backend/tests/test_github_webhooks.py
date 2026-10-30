import hashlib
import hmac
import json

from tests.conftest import register


def _sign(body: bytes) -> str:
    digest = hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_webhook_rejects_bad_signature(client):
    body = json.dumps({"action": "opened"}).encode()
    response = client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": "sha256=deadbeef",
            "X-GitHub-Delivery": "d1",
            "X-GitHub-Event": "push",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 401


def test_webhook_duplicate_delivery(client):
    body = json.dumps({"repository": {"full_name": "acme/api"}}).encode()
    headers = {
        "X-Hub-Signature-256": _sign(body),
        "X-GitHub-Delivery": "delivery-1",
        "X-GitHub-Event": "push",
        "Content-Type": "application/json",
    }
    first = client.post("/api/v1/webhooks/github", content=body, headers=headers)
    second = client.post("/api/v1/webhooks/github", content=body, headers=headers)
    assert first.status_code == 200
    assert second.json()["duplicate"] is True


def test_github_status_disconnected_without_config(client):
    register(client)
    status = client.get("/api/v1/github/status")
    assert status.status_code == 200
    assert status.json()["configured"] is False
    assert status.json()["connected"] is False
    assert status.json()["oauth"]["status"] == "missing"


def test_webhook_uninstall_disconnects_repos(client):
    from codecare.db import SessionLocal
    from codecare.models import GithubInstallation, OrganizationMember, Repository, new_id

    register(client)
    db = SessionLocal()
    member = db.query(OrganizationMember).first()
    installation = GithubInstallation(
        id=new_id(),
        organization_id=member.organization_id,
        installation_id="555",
        account_login="acme",
    )
    db.add(installation)
    db.flush()
    repo = Repository(
        id=new_id(),
        organization_id=member.organization_id,
        installation_id=installation.id,
        github_repo_id="1",
        full_name="acme/api",
        connected=True,
    )
    db.add(repo)
    db.commit()
    db.close()
    body = json.dumps({"action": "deleted", "installation": {"id": 555}}).encode()
    response = client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": _sign(body),
            "X-GitHub-Delivery": "uninstall-1",
            "X-GitHub-Event": "installation",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200
    db = SessionLocal()
    stored = db.query(Repository).filter_by(full_name="acme/api").one()
    assert stored.connected is False
    db.close()


def test_install_url_requires_config(client):
    register(client)
    response = client.get("/api/v1/github/install-url")
    assert response.status_code == 503
