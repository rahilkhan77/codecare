from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from codecare.db import get_db
from codecare.deps import AuthContext, current_auth, org_scoped
from codecare.models import (
    DetectedIssue,
    FixProposal,
    MaintenanceJob,
    PullRequestRecord,
    Repository,
    RepositoryScan,
    VerificationRun,
    new_id,
)
from codecare.schemas import RepoScheduleUpdate
from codecare.services.entitlements import assert_metric_quota
from codecare.services.jobs import enqueue, has_active_job

router = APIRouter(tags=["maintenance"])


def _repo(row: Repository) -> dict:
    return {
        "id": row.id,
        "full_name": row.full_name,
        "default_branch": row.default_branch,
        "language": row.language,
        "health": row.health_status,
        "last_scanned_at": row.last_scanned_at.isoformat() if row.last_scanned_at else None,
        "private": row.private,
        "scan_interval_hours": row.scan_interval_hours,
    }


@router.get("/api/v1/repos")
def repos(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    rows = db.scalars(select(Repository).where(Repository.organization_id == auth.organization_id, Repository.connected.is_(True))).all()
    return [_repo(row) for row in rows]


@router.get("/api/v1/repos/{repo_id}")
def repo_detail(repo_id: str, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    repo = org_scoped(Repository, repo_id, auth, db)
    issues = db.scalars(
        select(DetectedIssue).where(DetectedIssue.repository_id == repo.id).order_by(DetectedIssue.created_at.desc()).limit(50)
    ).all()
    scans = db.scalars(
        select(RepositoryScan).where(RepositoryScan.repository_id == repo.id).order_by(RepositoryScan.created_at.desc()).limit(20)
    ).all()
    return {
        **_repo(repo),
        "issues": [_issue(item) for item in issues],
        "scans": [_scan(item) for item in scans],
    }


@router.post("/api/v1/repos/{repo_id}/scans")
def trigger_scan(repo_id: str, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    repo = org_scoped(Repository, repo_id, auth, db)
    if not repo.connected:
        raise HTTPException(status_code=400, detail="Repository is disconnected")
    assert_metric_quota(db, auth.organization_id, "scans", "scans_per_day")
    if has_active_job(db, repository_id=repo.id, kind="scan"):
        raise HTTPException(status_code=409, detail="A scan is already queued or running for this repository")
    scan = RepositoryScan(id=new_id(), organization_id=auth.organization_id, repository_id=repo.id, status="queued", trigger="manual")
    db.add(scan)
    db.commit()
    job = enqueue(db, organization_id=auth.organization_id, kind="scan", repository_id=repo.id, payload={"scan_id": scan.id})
    return {"scan_id": scan.id, "job_id": job.id, "status": "queued"}


@router.patch("/api/v1/repos/{repo_id}")
def update_repo(repo_id: str, payload: RepoScheduleUpdate, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    repo = org_scoped(Repository, repo_id, auth, db)
    repo.scan_interval_hours = payload.scan_interval_hours
    db.commit()
    return _repo(repo)


@router.get("/api/v1/repos/{repo_id}/scans")
def list_scans(repo_id: str, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    org_scoped(Repository, repo_id, auth, db)
    rows = db.scalars(select(RepositoryScan).where(RepositoryScan.repository_id == repo_id).order_by(RepositoryScan.created_at.desc())).all()
    return [_scan(row) for row in rows]


@router.get("/api/v1/scans/{scan_id}")
def scan_detail(scan_id: str, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    scan = org_scoped(RepositoryScan, scan_id, auth, db)
    issues = db.scalars(select(DetectedIssue).where(DetectedIssue.scan_id == scan.id)).all()
    return {**_scan(scan), "issues": [_issue(item) for item in issues]}


@router.get("/api/v1/issues")
def issues(repository_id: str | None = None, status: str | None = None, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    stmt = select(DetectedIssue).where(DetectedIssue.organization_id == auth.organization_id)
    if repository_id:
        stmt = stmt.where(DetectedIssue.repository_id == repository_id)
    if status:
        stmt = stmt.where(DetectedIssue.status == status)
    rows = db.scalars(stmt.order_by(DetectedIssue.created_at.desc()).limit(200)).all()
    return [_issue(row) for row in rows]


@router.get("/api/v1/issues/{issue_id}")
def issue_detail(issue_id: str, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    issue = org_scoped(DetectedIssue, issue_id, auth, db)
    proposals = db.scalars(select(FixProposal).where(FixProposal.issue_id == issue.id).order_by(FixProposal.created_at.desc())).all()
    return {**_issue(issue), "proposals": [_proposal(item) for item in proposals]}


@router.post("/api/v1/issues/{issue_id}/diagnose")
def diagnose_issue(issue_id: str, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    issue = org_scoped(DetectedIssue, issue_id, auth, db)
    assert_metric_quota(db, auth.organization_id, "ai_calls", "ai_per_day")
    job = enqueue(db, organization_id=auth.organization_id, kind="diagnose", repository_id=issue.repository_id, issue_id=issue.id)
    return {"job_id": job.id, "status": "queued"}


@router.post("/api/v1/issues/{issue_id}/propose-fix")
def propose(issue_id: str, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    issue = org_scoped(DetectedIssue, issue_id, auth, db)
    assert_metric_quota(db, auth.organization_id, "ai_calls", "ai_per_day")
    job = enqueue(db, organization_id=auth.organization_id, kind="propose_fix", repository_id=issue.repository_id, issue_id=issue.id)
    return {"job_id": job.id, "status": "queued"}


@router.get("/api/v1/fixes/{proposal_id}")
def fix_detail(proposal_id: str, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    proposal = org_scoped(FixProposal, proposal_id, auth, db)
    runs = db.scalars(select(VerificationRun).where(VerificationRun.proposal_id == proposal.id).order_by(VerificationRun.created_at.desc())).all()
    return {**_proposal(proposal), "verification_runs": [_run(item) for item in runs]}


@router.post("/api/v1/fixes/{proposal_id}/verify")
def verify(proposal_id: str, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    proposal = org_scoped(FixProposal, proposal_id, auth, db)
    job = enqueue(
        db,
        organization_id=auth.organization_id,
        kind="verify",
        repository_id=proposal.repository_id,
        issue_id=proposal.issue_id,
        payload={"proposal_id": proposal.id},
    )
    return {"job_id": job.id, "status": "queued"}


@router.post("/api/v1/fixes/{proposal_id}/pull-request")
def open_pr(proposal_id: str, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    proposal = org_scoped(FixProposal, proposal_id, auth, db)
    latest = db.scalar(
        select(VerificationRun).where(VerificationRun.proposal_id == proposal.id).order_by(VerificationRun.created_at.desc())
    )
    if not latest or latest.status != "verified":
        raise HTTPException(status_code=409, detail="Pull requests are created only after a verified sandbox run.")
    job = enqueue(
        db,
        organization_id=auth.organization_id,
        kind="pull_request",
        repository_id=proposal.repository_id,
        issue_id=proposal.issue_id,
        payload={"proposal_id": proposal.id},
    )
    return {"job_id": job.id, "status": "queued"}


@router.get("/api/v1/pull-requests")
def pull_requests(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(PullRequestRecord).where(PullRequestRecord.organization_id == auth.organization_id).order_by(PullRequestRecord.created_at.desc())
    ).all()
    return [
        {
            "id": row.id,
            "repository_id": row.repository_id,
            "number": row.number,
            "url": row.url,
            "title": row.title,
            "status": row.status,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.get("/api/v1/jobs/{job_id}")
def job_detail(job_id: str, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    job = org_scoped(MaintenanceJob, job_id, auth, db)
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "error": job.error,
        "result": json.loads(job.result or "{}") if (job.result or "").startswith("{") else job.result,
        "created_at": job.created_at.isoformat(),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def _scan(row: RepositoryScan) -> dict:
    return {
        "id": row.id,
        "repository_id": row.repository_id,
        "status": row.status,
        "trigger": row.trigger,
        "summary": row.summary,
        "languages": json.loads(row.languages or "[]"),
        "commit_sha": row.commit_sha,
        "error": row.error,
        "created_at": row.created_at.isoformat(),
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


def _issue(row: DetectedIssue) -> dict:
    return {
        "id": row.id,
        "repository_id": row.repository_id,
        "title": row.title,
        "category": row.category,
        "severity": row.severity,
        "status": row.status,
        "file_path": row.file_path,
        "evidence": row.evidence,
        "diagnosis": row.diagnosis,
        "created_at": row.created_at.isoformat(),
    }


def _proposal(row: FixProposal) -> dict:
    try:
        files = list(json.loads(row.patch or "{}").keys())
    except json.JSONDecodeError:
        files = []
    return {
        "id": row.id,
        "issue_id": row.issue_id,
        "summary": row.summary,
        "provider": row.provider,
        "status": row.status,
        "files": files,
        "patch": json.loads(row.patch) if row.patch.startswith("{") else row.patch,
        "created_at": row.created_at.isoformat(),
    }


def _run(row: VerificationRun) -> dict:
    return {
        "id": row.id,
        "status": row.status,
        "log": row.log,
        "tests_passed": row.tests_passed,
        "tests_failed": row.tests_failed,
        "created_at": row.created_at.isoformat(),
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }
