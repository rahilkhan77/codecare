"""scan schedules and job attempts

Revision ID: 002
Revises: 001
Create Date: 2026-10-30
"""

from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("repositories", sa.Column("scan_interval_hours", sa.Integer(), nullable=False, server_default="24"))
    op.add_column("maintenance_jobs", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("maintenance_jobs", "attempts")
    op.drop_column("repositories", "scan_interval_hours")
