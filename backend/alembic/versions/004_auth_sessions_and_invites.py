"""sessions, email verification, invites, oauth login

Revision ID: 004
Revises: 003
Create Date: 2026-10-30
"""

from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("profiles", sa.Column("github_user_id", sa.String(32), nullable=True))
    op.add_column("profiles", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_profiles_github_user_id", "profiles", ["github_user_id"], unique=True)
    op.add_column(
        "subscriptions",
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("oauth_states", sa.Column("purpose", sa.String(32), nullable=False, server_default="connect"))
    op.alter_column("oauth_states", "profile_id", existing_type=sa.String(36), nullable=True)
    op.alter_column("oauth_states", "organization_id", existing_type=sa.String(36), nullable=True)

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("profile_id", sa.String(36), sa.ForeignKey("profiles.id"), nullable=False),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_sessions_profile_id", "auth_sessions", ["profile_id"])
    op.create_index("ix_auth_sessions_organization_id", "auth_sessions", ["organization_id"])

    op.create_table(
        "auth_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("profile_id", sa.String(36), sa.ForeignKey("profiles.id"), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_tokens_profile_id", "auth_tokens", ["profile_id"])
    op.create_index("ix_auth_tokens_token_hash", "auth_tokens", ["token_hash"], unique=True)

    op.create_table(
        "organization_invites",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("invited_by", sa.String(36), sa.ForeignKey("profiles.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_organization_invites_organization_id", "organization_invites", ["organization_id"])
    op.create_index("ix_organization_invites_email", "organization_invites", ["email"])
    op.create_index("ix_organization_invites_token_hash", "organization_invites", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_table("organization_invites")
    op.drop_table("auth_tokens")
    op.drop_table("auth_sessions")
    op.alter_column("oauth_states", "organization_id", existing_type=sa.String(36), nullable=False)
    op.alter_column("oauth_states", "profile_id", existing_type=sa.String(36), nullable=False)
    op.drop_column("oauth_states", "purpose")
    op.drop_column("subscriptions", "cancel_at_period_end")
    op.drop_index("ix_profiles_github_user_id", table_name="profiles")
    op.drop_column("profiles", "email_verified_at")
    op.drop_column("profiles", "github_user_id")
