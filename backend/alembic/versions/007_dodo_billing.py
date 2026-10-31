"""replace razorpay columns with dodo payments

Revision ID: 007
Revises: 006
Create Date: 2026-10-31
"""

import sqlalchemy as sa
from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "subscriptions",
        "razorpay_customer_id",
        new_column_name="dodo_customer_id",
        existing_type=sa.String(64),
        type_=sa.String(128),
        existing_nullable=False,
    )
    op.alter_column(
        "subscriptions",
        "razorpay_subscription_id",
        new_column_name="dodo_subscription_id",
        existing_type=sa.String(64),
        type_=sa.String(128),
        existing_nullable=False,
    )
    op.rename_table("razorpay_webhook_events", "dodo_webhook_events")
    op.drop_index("ix_razorpay_webhook_events_event_id", table_name="dodo_webhook_events")
    op.alter_column(
        "dodo_webhook_events",
        "event_id",
        existing_type=sa.String(80),
        type_=sa.String(255),
        existing_nullable=False,
    )
    op.create_index("ix_dodo_webhook_events_event_id", "dodo_webhook_events", ["event_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_dodo_webhook_events_event_id", table_name="dodo_webhook_events")
    op.alter_column(
        "dodo_webhook_events",
        "event_id",
        existing_type=sa.String(255),
        type_=sa.String(80),
        existing_nullable=False,
    )
    op.create_index("ix_razorpay_webhook_events_event_id", "dodo_webhook_events", ["event_id"], unique=True)
    op.rename_table("dodo_webhook_events", "razorpay_webhook_events")
    op.alter_column(
        "subscriptions",
        "dodo_subscription_id",
        new_column_name="razorpay_subscription_id",
        existing_type=sa.String(128),
        type_=sa.String(64),
        existing_nullable=False,
    )
    op.alter_column(
        "subscriptions",
        "dodo_customer_id",
        new_column_name="razorpay_customer_id",
        existing_type=sa.String(128),
        type_=sa.String(64),
        existing_nullable=False,
    )
