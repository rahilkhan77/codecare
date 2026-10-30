"""oauth token expiry and refresh fields

Revision ID: 003
Revises: 002
Create Date: 2026-10-30
"""

from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("github_installations", sa.Column("encrypted_refresh_token", sa.Text(), nullable=True))
    op.add_column("github_installations", sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("github_installations", sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("github_installations", sa.Column("token_status", sa.String(32), nullable=False, server_default="missing"))


def downgrade() -> None:
    op.drop_column("github_installations", "token_status")
    op.drop_column("github_installations", "refresh_expires_at")
    op.drop_column("github_installations", "token_expires_at")
    op.drop_column("github_installations", "encrypted_refresh_token")
