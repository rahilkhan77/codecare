"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-10-30
"""

from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("avatar_url", sa.String(500), nullable=False, server_default=""),
        sa.Column("supabase_user_id", sa.String(64)),
        sa.Column("password_hash", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_profiles_email", "profiles", ["email"], unique=True)
    op.create_index("ix_profiles_supabase_user_id", "profiles", ["supabase_user_id"], unique=True)

    op.create_table(
        "organizations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("profiles.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"], unique=True)

    op.create_table(
        "organization_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("profile_id", sa.String(36), sa.ForeignKey("profiles.id"), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="owner"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "profile_id"),
    )

    op.create_table(
        "github_installations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("installation_id", sa.String(32), nullable=False),
        sa.Column("account_login", sa.String(200), nullable=False, server_default=""),
        sa.Column("account_type", sa.String(32), nullable=False, server_default="User"),
        sa.Column("encrypted_user_token", sa.Text()),
        sa.Column("github_user_id", sa.String(32)),
        sa.Column("github_login", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_github_installations_installation_id", "github_installations", ["installation_id"], unique=True)

    op.create_table(
        "repositories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("installation_id", sa.String(36), sa.ForeignKey("github_installations.id")),
        sa.Column("github_repo_id", sa.String(32), nullable=False),
        sa.Column("full_name", sa.String(300), nullable=False),
        sa.Column("default_branch", sa.String(200), nullable=False, server_default="main"),
        sa.Column("private", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("language", sa.String(80), nullable=False, server_default=""),
        sa.Column("connected", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("health_status", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("last_scanned_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "github_repo_id"),
    )

    op.create_table(
        "repository_scans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("repository_id", sa.String(36), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("trigger", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("languages", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("commit_sha", sa.String(64), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "detected_issues",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("repository_id", sa.String(36), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("scan_id", sa.String(36), sa.ForeignKey("repository_scans.id")),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("file_path", sa.String(500), nullable=False, server_default=""),
        sa.Column("evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("diagnosis", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "maintenance_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("repository_id", sa.String(36), sa.ForeignKey("repositories.id")),
        sa.Column("issue_id", sa.String(36), sa.ForeignKey("detected_issues.id")),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("result", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "fix_proposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("repository_id", sa.String(36), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("issue_id", sa.String(36), sa.ForeignKey("detected_issues.id"), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("maintenance_jobs.id")),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("patch", sa.Text(), nullable=False, server_default=""),
        sa.Column("provider", sa.String(64), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="proposed"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "verification_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("repository_id", sa.String(36), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("proposal_id", sa.String(36), sa.ForeignKey("fix_proposals.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("log", sa.Text(), nullable=False, server_default=""),
        sa.Column("tests_passed", sa.Integer()),
        sa.Column("tests_failed", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "pull_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("repository_id", sa.String(36), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("proposal_id", sa.String(36), sa.ForeignKey("fix_proposals.id"), nullable=False),
        sa.Column("verification_id", sa.String(36), sa.ForeignKey("verification_runs.id")),
        sa.Column("number", sa.Integer()),
        sa.Column("url", sa.String(500), nullable=False, server_default=""),
        sa.Column("title", sa.String(300), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "usage_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False, unique=True),
        sa.Column("plan", sa.String(32), nullable=False, server_default="none"),
        sa.Column("status", sa.String(32), nullable=False, server_default="inactive"),
        sa.Column("stripe_customer_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("stripe_subscription_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("current_period_end", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("profile_id", sa.String(36), sa.ForeignKey("profiles.id")),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id")),
        sa.Column("profile_id", sa.String(36), sa.ForeignKey("profiles.id")),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False, server_default=""),
        sa.Column("target_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "github_webhook_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("delivery_id", sa.String(64), nullable=False),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_github_webhook_events_delivery_id", "github_webhook_events", ["delivery_id"], unique=True)

    op.create_table(
        "contact_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("topic", sa.String(64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "oauth_states",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("state", sa.String(64), nullable=False),
        sa.Column("profile_id", sa.String(36), sa.ForeignKey("profiles.id"), nullable=False),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_oauth_states_state", "oauth_states", ["state"], unique=True)


def downgrade() -> None:
    for table in [
        "oauth_states",
        "contact_messages",
        "github_webhook_events",
        "audit_logs",
        "notifications",
        "subscriptions",
        "usage_records",
        "pull_requests",
        "verification_runs",
        "fix_proposals",
        "maintenance_jobs",
        "detected_issues",
        "repository_scans",
        "repositories",
        "github_installations",
        "organization_members",
        "organizations",
        "profiles",
    ]:
        op.drop_table(table)
