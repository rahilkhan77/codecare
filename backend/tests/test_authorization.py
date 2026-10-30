from unittest.mock import AsyncMock

from tests.conftest import register


def test_cannot_read_other_org_issue(client):
    register(client, "one@example.com")
    overview = client.get("/api/v1/workspace/overview")
    assert overview.status_code == 200
    client.post("/api/v1/auth/logout")
    register(client, "two@example.com")
    missing = client.get("/api/v1/issues/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404


def test_unauthenticated_workspace(client):
    assert client.get("/api/v1/repos").status_code == 401
    assert client.get("/api/v1/workspace/overview").status_code == 401


def test_connect_rejects_repo_outside_installation(client, monkeypatch):
    register(client)
    from codecare.db import SessionLocal
    from codecare.models import GithubInstallation, OrganizationMember, new_id

    db = SessionLocal()
    member = db.query(OrganizationMember).first()
    db.add(GithubInstallation(id=new_id(), organization_id=member.organization_id, installation_id="4242"))
    db.commit()
    db.close()
    monkeypatch.setattr("codecare.routers.github.gh.installation_token", AsyncMock(return_value="token"))
    monkeypatch.setattr("codecare.routers.github.gh.list_installation_repos", AsyncMock(return_value=[]))
    response = client.post("/api/v1/github/repos/connect", json={"github_repo_ids": ["999"]})
    assert response.status_code == 403
