from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from codecare.models import (
    AuditLog,
    AuthSession,
    AuthToken,
    DetectedIssue,
    FixProposal,
    GithubInstallation,
    MaintenanceJob,
    Notification,
    OauthState,
    Organization,
    OrganizationInvite,
    OrganizationMember,
    Profile,
    PullRequestRecord,
    Repository,
    RepositoryScan,
    Subscription,
    UsageRecord,
    VerificationRun,
)


class AccountGuardError(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def _purge_organization(db: Session, organization_id: str) -> None:
    repo_ids = list(db.scalars(select(Repository.id).where(Repository.organization_id == organization_id)).all())
    if repo_ids:
        db.execute(delete(PullRequestRecord).where(PullRequestRecord.organization_id == organization_id))
        db.execute(delete(VerificationRun).where(VerificationRun.organization_id == organization_id))
        db.execute(delete(FixProposal).where(FixProposal.organization_id == organization_id))
        db.execute(delete(MaintenanceJob).where(MaintenanceJob.organization_id == organization_id))
        db.execute(delete(DetectedIssue).where(DetectedIssue.organization_id == organization_id))
        db.execute(delete(RepositoryScan).where(RepositoryScan.organization_id == organization_id))
        db.execute(delete(Repository).where(Repository.organization_id == organization_id))
    db.execute(delete(GithubInstallation).where(GithubInstallation.organization_id == organization_id))
    db.execute(delete(UsageRecord).where(UsageRecord.organization_id == organization_id))
    db.execute(delete(Subscription).where(Subscription.organization_id == organization_id))
    db.execute(delete(Notification).where(Notification.organization_id == organization_id))
    db.execute(delete(AuditLog).where(AuditLog.organization_id == organization_id))
    db.execute(delete(OauthState).where(OauthState.organization_id == organization_id))
    db.execute(delete(OrganizationInvite).where(OrganizationInvite.organization_id == organization_id))
    db.execute(delete(AuthSession).where(AuthSession.organization_id == organization_id))
    db.execute(delete(OrganizationMember).where(OrganizationMember.organization_id == organization_id))
    db.execute(delete(Organization).where(Organization.id == organization_id))


def delete_account(db: Session, profile: Profile) -> None:
    memberships = list(db.scalars(select(OrganizationMember).where(OrganizationMember.profile_id == profile.id)).all())

    def others_exist(organization_id: str) -> bool:
        return (
            db.scalar(
                select(OrganizationMember.id).where(
                    OrganizationMember.organization_id == organization_id,
                    OrganizationMember.profile_id != profile.id,
                )
            )
            is not None
        )

    for member in memberships:
        if member.role == "owner" and others_exist(member.organization_id):
            raise AccountGuardError("Transfer ownership before deleting an account that still has other members.")
    for member in memberships:
        if not others_exist(member.organization_id):
            _purge_organization(db, member.organization_id)
        else:
            db.delete(member)
    db.execute(delete(OrganizationMember).where(OrganizationMember.profile_id == profile.id))
    db.execute(delete(AuthSession).where(AuthSession.profile_id == profile.id))
    db.execute(delete(AuthToken).where(AuthToken.profile_id == profile.id))
    db.execute(delete(OauthState).where(OauthState.profile_id == profile.id))
    db.execute(delete(OrganizationInvite).where(OrganizationInvite.invited_by == profile.id))
    db.execute(update(Notification).where(Notification.profile_id == profile.id).values(profile_id=None))
    db.execute(update(AuditLog).where(AuditLog.profile_id == profile.id).values(profile_id=None))
    db.execute(delete(Profile).where(Profile.id == profile.id))
