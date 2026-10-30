from codecare.production import validate_settings
from codecare.services.github import GitHubError, safe_repo_path
from tests.conftest import register


def test_github_status_not_connected_without_install(client):
    register(client)
    status = client.get("/api/v1/github/status").json()
    assert status["connected"] is False
    assert status["configured"] is False


def test_path_traversal_rejected():
    try:
        safe_repo_path("../secrets.env")
        assert False
    except GitHubError:
        pass
    assert safe_repo_path("src/app.py") == "src/app.py"


def test_production_rejects_weak_secret():
    from codecare.config import Settings

    settings = Settings.model_construct(
        app_env="production",
        app_secret="dev-only-change-me",
        credentials_encryption_key="",
        database_url="postgresql+psycopg2://codecare:codecare@localhost/codecare",
        api_cors_origins="http://127.0.0.1:8000",
    )
    try:
        validate_settings(settings)
        assert False
    except RuntimeError as exc:
        assert "APP_SECRET" in str(exc)


def test_readyz(client):
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_duplicate_scan_conflict(client):
    register(client)
    from codecare.db import SessionLocal
    from codecare.models import GithubInstallation, OrganizationMember, Repository, new_id

    db = SessionLocal()
    member = db.query(OrganizationMember).first()
    installation = GithubInstallation(id=new_id(), organization_id=member.organization_id, installation_id="999")
    repo = Repository(
        id=new_id(),
        organization_id=member.organization_id,
        installation_id=installation.id,
        github_repo_id="2",
        full_name="acme/web",
        connected=True,
    )
    db.add_all([installation, repo])
    db.commit()
    first = client.post(f"/api/v1/repos/{repo.id}/scans")
    second = client.post(f"/api/v1/repos/{repo.id}/scans")
    assert first.status_code == 200
    assert second.status_code == 409
    db.close()
