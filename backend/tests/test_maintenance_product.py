from tests.conftest import register


def test_plan_catalog_is_app_bundles(client):
    plans = client.get("/api/v1/billing/plans").json()["plans"]
    by_id = {plan["id"]: plan for plan in plans}
    assert by_id["starter"]["repos"] == 1
    assert by_id["indie"]["repos"] == 5
    assert by_id["studio"]["repos"] is None
    assert by_id["indie"]["monthly"] == 79


def test_request_fix_waits_without_github(client):
    register(client)
    from codecare.db import SessionLocal
    from codecare.models import OrganizationMember, Repository, new_id

    db = SessionLocal()
    member = db.query(OrganizationMember).one()
    repo = Repository(
        id=new_id(),
        organization_id=member.organization_id,
        github_repo_id="local-1",
        full_name="sample/side-project",
        connected=True,
    )
    db.add(repo)
    db.commit()
    repo_id = repo.id
    db.close()
    response = client.post(
        f"/api/v1/repos/{repo_id}/requests",
        json={"description": "The signup button does nothing after I tap it twice."},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["queued"] is False
    assert "GitHub" in body["detail"]
    issues = client.get("/api/v1/issues").json()
    assert issues[0]["category"] == "request"
    assert "signup button" in issues[0]["evidence"]


def test_schedule_and_auto_merge_opt_in(client):
    register(client)
    from codecare.db import SessionLocal
    from codecare.models import OrganizationMember, Repository, new_id

    db = SessionLocal()
    member = db.query(OrganizationMember).one()
    repo = Repository(
        id=new_id(),
        organization_id=member.organization_id,
        github_repo_id="local-2",
        full_name="sample/other-app",
        connected=True,
    )
    db.add(repo)
    db.commit()
    repo_id = repo.id
    db.close()
    updated = client.patch(f"/api/v1/repos/{repo_id}", json={"scan_interval_hours": 168, "auto_merge": True})
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["scan_interval_hours"] == 168
    assert body["auto_merge"] is True


def test_monthly_report_and_onboarding(client):
    registered = register(client)
    me = client.get("/api/v1/auth/me").json()
    assert me["onboarding_completed"] is False
    report = client.get("/api/v1/reports/monthly")
    assert report.status_code == 200
    assert "pull request" in report.json()["summary"]
    mailed = client.post("/api/v1/reports/monthly/email")
    assert mailed.status_code == 200
    assert mailed.json()["delivery"] == "console"
    done = client.post("/api/v1/onboarding/complete")
    assert done.status_code == 200
    assert client.get("/api/v1/auth/me").json()["onboarding_completed"] is True
    assert registered["organization_id"]
