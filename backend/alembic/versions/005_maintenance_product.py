"""per-repo auto-merge and onboarding completion

Revision ID: 005
Revises: 004
Create Date: 2026-10-30
"""

from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("repositories", sa.Column("auto_merge", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("profiles", sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("profiles", "onboarding_completed_at")
    op.drop_column("repositories", "auto_merge")
