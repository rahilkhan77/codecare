from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from codecare.db import SessionLocal
from codecare.models import DetectedIssue, GithubInstallation, OrganizationMember, Repository, new_id
from codecare.services.jobs import enqueue, run_job
from tests.conftest import register


def _seed_repo(email: str = "one@example.com"):
    db = SessionLocal()
    member = db.query(OrganizationMember).filter(OrganizationMember.profile_id != None).first()  # noqa: E711
    installation = GithubInstallation(
        id=new_id(),
        organization_id=member.organization_id,
        installation_id="12345",
        account_login="acme",
    )
    db.add(installation)
    db.flush()
    repo = Repository(
        id=new_id(),
        organization_id=member.organization_id,
        installation_id=installation.id,
        github_repo_id="99",
        full_name="acme/api",
        connected=True,
    )
    db.add(repo)
    db.flush()
    issue = DetectedIssue(
        id=new_id(),
        organization_id=member.organization_id,
        repository_id=repo.id,
        fingerprint="fp1",
        title="eval used",
        category="static_analysis",
        file_path="app.py",
        evidence="eval(",
    )
    db.add(issue)
    db.commit()
    ids = (member.organization_id, repo.id, issue.id)
    db.close()
    return ids


def test_verify_and_pr_pipeline_mocked(client):
    register(client, "pipe@example.com")
    org_id, repo_id, issue_id = _seed_repo()
    db = SessionLocal()
    job = enqueue(db, organization_id=org_id, kind="propose_fix", repository_id=repo_id, issue_id=issue_id)

    async def fake_token(_id):
        return "token"

    with (
        patch("codecare.services.jobs._installation_token_for", new=AsyncMock(return_value="token")),
        patch("codecare.services.jobs.gh.get_file", new=AsyncMock(return_value="print(1)\n")),
        patch(
            "codecare.services.jobs.propose_fix",
            new=AsyncMock(return_value=("groq", "fix eval", {"app.py": "print(1)\n"}, True)),
        ),
    ):
        asyncio.run(run_job(SessionLocal(), job.id))

    proposals = client.get(f"/api/v1/issues/{issue_id}").json()["proposals"]
    assert proposals
    proposal_id = proposals[0]["id"]
    verify = client.post(f"/api/v1/fixes/{proposal_id}/verify")
    assert verify.status_code == 200
    with (
        patch("codecare.services.jobs._installation_token_for", new=AsyncMock(return_value="token")),
        patch("codecare.services.jobs.gh.download_tarball", new=AsyncMock(return_value=1)),
        patch("codecare.services.jobs.verify_patch", return_value={"status": "verified", "log": "ok", "tests_passed": 1, "tests_failed": 0}),
    ):
        asyncio.run(run_job(SessionLocal(), verify.json()["job_id"]))

    pr = client.post(f"/api/v1/fixes/{proposal_id}/pull-request")
    assert pr.status_code == 200
    with (
        patch("codecare.services.jobs._installation_token_for", new=AsyncMock(return_value="token")),
        patch("codecare.services.jobs.gh.get_repo", new=AsyncMock(return_value={"default_branch": "main"})),
        patch("codecare.services.jobs.gh._request", new=AsyncMock(return_value={"commit": {"sha": "abc"}})),
        patch("codecare.services.jobs.gh.create_branch_and_commit", new=AsyncMock()),
        patch("codecare.services.jobs.gh.create_pull_request", new=AsyncMock(return_value={"number": 7, "html_url": "https://github.com/acme/api/pull/7", "title": "fix"})),
    ):
        asyncio.run(run_job(SessionLocal(), pr.json()["job_id"]))

    rows = client.get("/api/v1/pull-requests").json()
    assert rows[0]["url"] == "https://github.com/acme/api/pull/7"


def test_limited_verification_does_not_open_a_pr(client):
    register(client, "limited@example.com")
    org_id, repo_id, issue_id = _seed_repo()
    db = SessionLocal()
    from codecare.models import FixProposal, VerificationRun

    proposal = FixProposal(
        id=new_id(),
        organization_id=org_id,
        repository_id=repo_id,
        issue_id=issue_id,
        summary="parsed only",
        patch="{}",
        provider="groq",
        status="proposed",
    )
    db.add(proposal)
    db.flush()
    db.add(
        VerificationRun(
            id=new_id(),
            organization_id=org_id,
            repository_id=repo_id,
            proposal_id=proposal.id,
            status="limited",
            log="LIMITED VERIFICATION",
        )
    )
    db.commit()
    proposal_id = proposal.id
    db.close()
    response = client.post(f"/api/v1/fixes/{proposal_id}/pull-request")
    assert response.status_code == 409


def test_unverified_pr_rejected(client):
    register(client, "unverified@example.com")
    org_id, repo_id, issue_id = _seed_repo()
    db = SessionLocal()
    from codecare.models import FixProposal

    proposal = FixProposal(
        id=new_id(),
        organization_id=org_id,
        repository_id=repo_id,
        issue_id=issue_id,
        summary="no",
        patch="{}",
        provider="heuristic",
        status="proposed",
    )
    db.add(proposal)
    db.commit()
    proposal_id = proposal.id
    db.close()
    response = client.post(f"/api/v1/fixes/{proposal_id}/pull-request")
    assert response.status_code == 409
