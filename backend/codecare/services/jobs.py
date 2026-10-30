from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from codecare.config import get_settings
from codecare.models import (
    DetectedIssue,
    FixProposal,
    MaintenanceJob,
    PullRequestRecord,
    Repository,
    RepositoryScan,
    VerificationRun,
    new_id,
    utcnow,
)
from codecare.security import redact
from codecare.services import github as gh
from codecare.services.ai import diagnose, propose_fix
from codecare.services.audit import notify, usage
from codecare.services.sandbox import verify_patch


def enqueue(
    db: Session,
    *,
    organization_id: str,
    kind: str,
    repository_id: str | None = None,
    issue_id: str | None = None,
    payload: dict | None = None,
) -> MaintenanceJob:
    job = MaintenanceJob(
        id=new_id(),
        organization_id=organization_id,
        repository_id=repository_id,
        issue_id=issue_id,
        kind=kind,
        status="queued",
        payload=json.dumps(payload or {}),
        attempts=0,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def has_active_job(db: Session, *, repository_id: str, kind: str) -> bool:
    row = db.scalar(
        select(MaintenanceJob).where(
            MaintenanceJob.repository_id == repository_id,
            MaintenanceJob.kind == kind,
            MaintenanceJob.status.in_(("queued", "running")),
        )
    )
    return row is not None


def enqueue_scan(db: Session, repo: Repository, trigger: str = "manual") -> tuple[RepositoryScan, MaintenanceJob] | tuple[None, None]:
    if has_active_job(db, repository_id=repo.id, kind="scan"):
        return None, None
    scan = RepositoryScan(
        id=new_id(),
        organization_id=repo.organization_id,
        repository_id=repo.id,
        status="queued",
        trigger=trigger,
    )
    db.add(scan)
    db.commit()
    job = enqueue(
        db,
        organization_id=repo.organization_id,
        kind="scan",
        repository_id=repo.id,
        payload={"scan_id": scan.id},
    )
    return scan, job


def schedule_due_scans(db: Session) -> int:
    now = datetime.now(timezone.utc)
    repos = db.scalars(select(Repository).where(Repository.connected.is_(True))).all()
    created = 0
    default_interval = get_settings().scan_interval_hours
    for repo in repos:
        hours = repo.scan_interval_hours or default_interval
        due = repo.last_scanned_at is None or (
            (repo.last_scanned_at if repo.last_scanned_at.tzinfo else repo.last_scanned_at.replace(tzinfo=timezone.utc))
            <= now - timedelta(hours=hours)
        )
        recent = db.scalar(
            select(MaintenanceJob).where(
                MaintenanceJob.repository_id == repo.id,
                MaintenanceJob.kind == "scan",
                MaintenanceJob.created_at >= now - timedelta(minutes=30),
            )
        )
        if due and not recent:
            scan, job = enqueue_scan(db, repo, trigger="scheduled")
            if job:
                created += 1
    return created


async def run_job(db: Session, job_id: str) -> None:
    job = db.get(MaintenanceJob, job_id)
    if not job:
        return
    job.status = "running"
    job.attempts = (job.attempts or 0) + 1
    db.commit()
    try:
        if job.kind == "scan":
            await _run_scan(db, job)
        elif job.kind == "diagnose":
            await _run_diagnose(db, job)
        elif job.kind == "propose_fix":
            await _run_fix(db, job)
        elif job.kind == "verify":
            await _run_verify(db, job)
        elif job.kind == "pull_request":
            await _run_pr(db, job)
        else:
            raise RuntimeError(f"Unknown job kind {job.kind}")
        job.status = "completed"
        job.completed_at = utcnow()
        db.commit()
    except Exception as exc:
        job.error = redact(str(exc))
        job.completed_at = utcnow()
        transient = "GitHub API 5" in str(exc) or "failed after retries" in str(exc)
        if transient and job.attempts < get_settings().max_job_attempts:
            job.status = "queued"
            job.completed_at = None
        else:
            job.status = "failed"
        db.commit()


async def _installation_token_for(db: Session, repo: Repository) -> str:
    from codecare.models import GithubInstallation

    installation = db.get(GithubInstallation, repo.installation_id) if repo.installation_id else None
    if not installation or str(installation.installation_id).startswith("pending-"):
        raise RuntimeError("Repository has no GitHub App installation")
    return await gh.installation_token(installation.installation_id)


async def _run_scan(db: Session, job: MaintenanceJob) -> None:
    from codecare.services.scanner import scan_repository

    repo = db.get(Repository, job.repository_id)
    payload = json.loads(job.payload or "{}")
    scan = db.get(RepositoryScan, payload["scan_id"]) if payload.get("scan_id") else None
    if not scan:
        scan = db.scalar(select(RepositoryScan).where(RepositoryScan.repository_id == repo.id).order_by(RepositoryScan.created_at.desc()))
    token = await _installation_token_for(db, repo)
    result = await scan_repository(token, {"full_name": repo.full_name, "default_branch": repo.default_branch})
    if scan:
        scan.status = "completed"
        scan.languages = json.dumps(result["languages"])
        scan.commit_sha = result["commit_sha"]
        scan.summary = f"{len(result['findings'])} findings across {result['file_count']} files"
        scan.completed_at = utcnow()
    repo.language = ", ".join(result["languages"][:3])
    repo.last_scanned_at = utcnow()
    open_high = 0
    for finding in result["findings"]:
        existing = db.scalar(
            select(DetectedIssue).where(
                DetectedIssue.organization_id == repo.organization_id,
                DetectedIssue.fingerprint == finding["fingerprint"],
            )
        )
        if existing:
            existing.status = "open"
            existing.evidence = finding["evidence"]
            existing.updated_at = utcnow()
        else:
            db.add(
                DetectedIssue(
                    id=new_id(),
                    organization_id=repo.organization_id,
                    repository_id=repo.id,
                    scan_id=scan.id if scan else None,
                    fingerprint=finding["fingerprint"],
                    title=finding["title"],
                    category=finding["category"],
                    severity=finding["severity"],
                    file_path=finding.get("file_path", ""),
                    evidence=finding["evidence"],
                )
            )
        if finding["severity"] in {"high", "critical"}:
            open_high += 1
    repo.health_status = "attention" if open_high else "healthy"
    usage(db, repo.organization_id, "scans")
    notify(db, repo.organization_id, "Scan completed", f"{repo.full_name}: {scan.summary if scan else 'done'}")
    job.result = json.dumps({"findings": len(result["findings"]), "scan_id": scan.id if scan else None})
    db.commit()


async def _collect_context(token: str, repo: Repository, issue: DetectedIssue) -> dict[str, str]:
    files: dict[str, str] = {}
    candidates = [issue.file_path] if issue.file_path and "/" not in issue.file_path[:1] else []
    for extra in ("README.md", "pyproject.toml", "package.json", "requirements.txt"):
        if extra not in candidates:
            candidates.append(extra)
    for path in candidates[:6]:
        if not path:
            continue
        try:
            content = await gh.get_file(token, repo.full_name, path, repo.default_branch)
        except gh.GitHubError:
            content = None
        if content:
            files[path] = content[:8000]
    return files


async def _run_diagnose(db: Session, job: MaintenanceJob) -> None:
    issue = db.get(DetectedIssue, job.issue_id)
    repo = db.get(Repository, issue.repository_id)
    token = await _installation_token_for(db, repo)
    files = await _collect_context(token, repo, issue)
    listing = "\n".join(f"{path}:\n{content}" for path, content in files.items())
    provider, text, is_model = await diagnose(issue.title, issue.evidence, f"repo={repo.full_name}\n{listing}")
    prefix = "" if is_model else "HEURISTIC_ONLY. "
    issue.diagnosis = f"{prefix}[{provider}] {text}"
    issue.status = "investigated"
    usage(db, repo.organization_id, "ai_calls")
    job.result = json.dumps({"provider": provider, "model_backed": is_model})
    db.commit()


async def _run_fix(db: Session, job: MaintenanceJob) -> None:
    issue = db.get(DetectedIssue, job.issue_id)
    repo = db.get(Repository, issue.repository_id)
    token = await _installation_token_for(db, repo)
    files = await _collect_context(token, repo, issue)
    if issue.file_path and issue.file_path not in files:
        try:
            content = await gh.get_file(token, repo.full_name, issue.file_path, repo.default_branch)
            if content:
                files[issue.file_path] = content
        except gh.GitHubError:
            pass
    provider, summary, proposed, is_model = await propose_fix(issue.title, issue.evidence, files)
    proposal = FixProposal(
        id=new_id(),
        organization_id=repo.organization_id,
        repository_id=repo.id,
        issue_id=issue.id,
        job_id=job.id,
        summary=summary,
        patch=json.dumps(proposed),
        provider=provider,
        status="proposed" if proposed else "insufficient_context",
    )
    db.add(proposal)
    issue.status = "fix_proposed" if proposed else "investigated"
    usage(db, repo.organization_id, "fix_proposals")
    usage(db, repo.organization_id, "ai_calls")
    job.result = json.dumps(
        {"proposal_id": proposal.id, "provider": provider, "files": list(proposed), "model_backed": is_model}
    )
    db.commit()


async def _run_verify(db: Session, job: MaintenanceJob) -> None:
    payload = json.loads(job.payload or "{}")
    proposal = db.get(FixProposal, payload["proposal_id"])
    files = json.loads(proposal.patch or "{}")
    repo = db.get(Repository, proposal.repository_id)
    work = Path(tempfile.mkdtemp(prefix="codecare-verify-"))
    try:
        token = await _installation_token_for(db, repo)
        if not files:
            result = {
                "status": "inconclusive",
                "log": "Proposal contains no files, so verification did not run.",
                "tests_passed": None,
                "tests_failed": None,
            }
        else:
            await gh.download_tarball(token, repo.full_name, repo.default_branch, work)
            result = verify_patch(files, work_dir=work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    run = VerificationRun(
        id=new_id(),
        organization_id=proposal.organization_id,
        repository_id=proposal.repository_id,
        proposal_id=proposal.id,
        status=result["status"],
        log=result["log"],
        tests_passed=result["tests_passed"],
        tests_failed=result["tests_failed"],
        completed_at=datetime.now(timezone.utc),
    )
    db.add(run)
    proposal.status = result["status"]
    job.result = json.dumps({"verification_id": run.id, "status": result["status"]})
    db.commit()


async def _run_pr(db: Session, job: MaintenanceJob) -> None:
    payload = json.loads(job.payload or "{}")
    proposal = db.get(FixProposal, payload["proposal_id"])
    latest = db.scalar(
        select(VerificationRun)
        .where(VerificationRun.proposal_id == proposal.id)
        .order_by(VerificationRun.created_at.desc())
    )
    if not latest or latest.status != "verified":
        raise RuntimeError("A GitHub pull request is only created after a verified sandbox run.")
    repo = db.get(Repository, proposal.repository_id)
    files = json.loads(proposal.patch or "{}")
    if not files:
        raise RuntimeError("Proposal contains no files to commit.")
    token = await _installation_token_for(db, repo)
    meta = await gh.get_repo(token, repo.full_name)
    branch_info = await gh._request(
        "GET",
        f"{gh.GITHUB_API}/repos/{repo.full_name}/branches/{meta.get('default_branch') or repo.default_branch}",
        token,
    )
    base_sha = branch_info["commit"]["sha"]
    branch = f"codecare/fix-{proposal.id[:8]}"
    await gh.create_branch_and_commit(
        token,
        repo.full_name,
        base_sha=base_sha,
        branch=branch,
        message=f"fix: {proposal.summary[:70]}",
        files=files,
    )
    auto_note = (
        "Auto-merge is enabled for this app. CodeCare will merge only if GitHub accepts the merge after verification."
        if repo.auto_merge
        else "Auto-merge is off. Review and merge this pull request yourself."
    )
    pr = await gh.create_pull_request(
        token,
        repo.full_name,
        title=f"CodeCare: {proposal.summary[:70]}",
        body=f"Proposed by CodeCare after an isolated verification run.\n\n{auto_note}",
        head=branch,
        base=meta.get("default_branch") or repo.default_branch,
    )
    status = "open"
    merge_error = ""
    if repo.auto_merge and pr.get("number"):
        try:
            merged = await gh.merge_pull_request(token, repo.full_name, int(pr["number"]))
            if merged.get("merged"):
                status = "merged"
            else:
                merge_error = merged.get("message") or "GitHub did not merge the pull request."
        except Exception as exc:
            merge_error = redact(str(exc))
    record = PullRequestRecord(
        id=new_id(),
        organization_id=repo.organization_id,
        repository_id=repo.id,
        proposal_id=proposal.id,
        verification_id=latest.id,
        number=pr.get("number"),
        url=pr.get("html_url") or "",
        title=pr.get("title") or "",
        status=status,
    )
    db.add(record)
    proposal.status = "pr_opened"
    notice = record.url if not merge_error else f"{record.url} Auto-merge did not complete: {merge_error}"
    notify(db, repo.organization_id, "Pull request merged" if status == "merged" else "Pull request opened", notice)
    job.result = json.dumps({"pull_request_id": record.id, "url": record.url, "status": status, "merge_error": merge_error})
    db.commit()
