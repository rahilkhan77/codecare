from codecare.models import DetectedIssue, Repository, new_id
from codecare.services.scanner import detect_languages, parse_package_json, parse_requirements
from tests.conftest import register


def test_language_and_manifest_parsing():
    langs = detect_languages(["src/main.py", "web/app.ts", "lib/util.go"])
    assert "Python" in langs
    pkgs = parse_requirements("requests==2.31.0\n# comment\nflask")
    assert pkgs[0][0] == "requests"
    _data, packages = parse_package_json('{"dependencies":{"express":"^4.18.2"}}')
    assert packages[0][0] == "express"


def test_repo_list_empty(client):
    register(client)
    repos = client.get("/api/v1/repos")
    assert repos.status_code == 200
    assert repos.json() == []


def test_tenant_scan_isolation(client):
    register(client)
    from codecare.db import SessionLocal
    from codecare.models import Organization, Profile

    db = SessionLocal()

    other_owner = Profile(id=new_id(), email="other@example.com", full_name="Other")
    db.add(other_owner)
    db.flush()
    other = Organization(id=new_id(), name="Other", slug=f"other-{other_owner.id[:8]}", owner_id=other_owner.id)
    db.add(other)
    db.flush()
    repo = Repository(
        id=new_id(),
        organization_id=other.id,
        github_repo_id="99",
        full_name="other/repo",
    )
    db.add(repo)
    db.flush()
    db.add(
        DetectedIssue(
            id=new_id(),
            organization_id=other.id,
            repository_id=repo.id,
            fingerprint="abc",
            title="secret",
            category="static_analysis",
        )
    )
    db.commit()
    issues = client.get("/api/v1/issues")
    assert issues.json() == []
    db.close()
