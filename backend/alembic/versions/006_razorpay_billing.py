"""replace stripe columns with razorpay

Revision ID: 006
Revises: 005
Create Date: 2026-10-30
"""

import sqlalchemy as sa
from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("subscriptions", "stripe_customer_id", new_column_name="razorpay_customer_id")
    op.alter_column("subscriptions", "stripe_subscription_id", new_column_name="razorpay_subscription_id")
    op.create_table(
        "razorpay_webhook_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(80), nullable=False),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_razorpay_webhook_events_event_id", "razorpay_webhook_events", ["event_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_razorpay_webhook_events_event_id", table_name="razorpay_webhook_events")
    op.drop_table("razorpay_webhook_events")
    op.alter_column("subscriptions", "razorpay_customer_id", new_column_name="stripe_customer_id")
    op.alter_column("subscriptions", "razorpay_subscription_id", new_column_name="stripe_subscription_id")
