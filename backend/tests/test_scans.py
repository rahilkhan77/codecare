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
    from codecare.models import OrganizationMember

    db = SessionLocal()
    member = db.query(OrganizationMember).first()
    other_org = "11111111-1111-1111-1111-111111111111"
    repo = Repository(
        id=new_id(),
        organization_id=other_org,
        github_repo_id="99",
        full_name="other/repo",
    )
    db.add(repo)
    db.add(
        DetectedIssue(
            id=new_id(),
            organization_id=other_org,
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
