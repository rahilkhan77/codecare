import pytest

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


def _enable_demo(monkeypatch):
    from codecare.config import get_settings

    monkeypatch.setattr(get_settings(), "demo_mode", True)


def test_demo_billing_upgrade_downgrade_and_cancel(client, monkeypatch):
    _enable_demo(monkeypatch)
    register(client, "payer@example.com")
    upgrade = client.post("/api/v1/billing/checkout", json={"plan": "indie"})
    assert upgrade.status_code == 200, upgrade.text
    assert upgrade.json()["demo"] is True
    assert "not charged" in upgrade.json()["detail"].lower() or "not charged" in upgrade.json()["detail"]
    assert client.get("/api/v1/billing/status").json()["plan"] == "indie"
    downgrade = client.post("/api/v1/billing/change-plan", json={"plan": "starter"})
    assert downgrade.status_code == 200
    assert client.get("/api/v1/billing/status").json()["plan"] == "starter"
    cancelled = client.post("/api/v1/billing/cancel")
    assert cancelled.status_code == 200
    assert client.get("/api/v1/billing/status").json()["cancel_at_period_end"] is True


def test_sample_connect_scan_request_and_automerge(client, monkeypatch):
    _enable_demo(monkeypatch)
    register(client, "maker@example.com")
    connected = client.post("/api/v1/github/repos/connect-sample")
    assert connected.status_code == 200, connected.text
    assert connected.json()["full_name"] == "sample/maintenance-fixture"
    repo_id = connected.json()["id"]
    saved = client.patch(f"/api/v1/repos/{repo_id}", json={"scan_interval_hours": 24, "auto_merge": True})
    assert saved.json()["auto_merge"] is True
    scan = client.post(f"/api/v1/repos/{repo_id}/scans")
    assert scan.status_code == 200, scan.text
    from codecare.db import SessionLocal
    from codecare.services.jobs import run_job

    db = SessionLocal()

    async def finish(job_id):
        await run_job(db, job_id)

    import asyncio

    asyncio.run(finish(scan.json()["job_id"]))
    db.close()
    issues = client.get("/api/v1/issues").json()
    assert any("eval" in item["title"].lower() for item in issues)
    asked = client.post(
        f"/api/v1/repos/{repo_id}/requests",
        json={"description": "The add function uses eval and I want it to just add the numbers."},
    )
    assert asked.status_code == 200, asked.text
    assert asked.json()["queued"] is True
    db = SessionLocal()
    asyncio.run(finish(asked.json()["job_id"]))
    db.close()
    overview = client.get("/api/v1/workspace/overview").json()
    card = next(item for item in overview["repositories"] if item["id"] == repo_id)
    assert "last_scanned_at" in card
    assert card["open_issues"] >= 1


def test_sample_pr_merges_only_after_verification(client, monkeypatch):
    _enable_demo(monkeypatch)
    register(client, "merge@example.com")
    repo_id = client.post("/api/v1/github/repos/connect-sample").json()["id"]
    client.patch(f"/api/v1/repos/{repo_id}", json={"scan_interval_hours": 24, "auto_merge": False})
    from codecare.db import SessionLocal
    from codecare.models import FixProposal, OrganizationMember, VerificationRun, new_id, utcnow
    from codecare.services.jobs import enqueue, run_job

    db = SessionLocal()
    member = db.query(OrganizationMember).one()
    proposal = FixProposal(
        id=new_id(),
        organization_id=member.organization_id,
        repository_id=repo_id,
        issue_id=new_id(),
        summary="[Sample] pin",
        patch="{}",
        provider="sample",
        status="proposed",
    )
    # issue_id FK: create a real issue first
    from codecare.models import DetectedIssue

    issue = DetectedIssue(
        id=new_id(),
        organization_id=member.organization_id,
        repository_id=repo_id,
        fingerprint="sample-merge",
        title="[Sample] eval",
        category="static_analysis",
        evidence="eval(",
    )
    db.add(issue)
    db.flush()
    org_id = member.organization_id
    issue_id = issue.id
    proposal_id = proposal.id
    proposal.issue_id = issue_id
    run = VerificationRun(
        id=new_id(),
        organization_id=member.organization_id,
        repository_id=repo_id,
        proposal_id=proposal_id,
        status="verified",
        log="[Sample] verified in test",
        tests_passed=1,
        tests_failed=0,
        completed_at=utcnow(),
    )
    db.add(proposal)
    db.flush()
    db.add(run)
    db.commit()
    job = enqueue(
        db,
        organization_id=org_id,
        kind="pull_request",
        repository_id=repo_id,
        issue_id=issue_id,
        payload={"proposal_id": proposal_id},
    )
    import asyncio

    asyncio.run(run_job(db, job.id))
    db.close()
    pulls = client.get("/api/v1/pull-requests").json()
    assert pulls[0]["status"] == "open"
    assert pulls[0]["title"].startswith("[Sample]")
    client.patch(f"/api/v1/repos/{repo_id}", json={"scan_interval_hours": 24, "auto_merge": True})
    db = SessionLocal()
    job = enqueue(
        db,
        organization_id=org_id,
        kind="pull_request",
        repository_id=repo_id,
        issue_id=issue_id,
        payload={"proposal_id": proposal_id},
    )
    asyncio.run(run_job(db, job.id))
    db.close()
    statuses = [row["status"] for row in client.get("/api/v1/pull-requests").json()]
    assert "merged" in statuses


def test_connect_sample_hidden_without_demo(client):
    register(client)
    assert client.post("/api/v1/github/repos/connect-sample").status_code == 404


def test_delete_account_after_signup(client):
    register(client, "gone@example.com")
    removed = client.post(
        "/api/v1/auth/delete-account",
        json={"password": "correct-horse-battery", "confirmation": "DELETE"},
    )
    assert removed.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
