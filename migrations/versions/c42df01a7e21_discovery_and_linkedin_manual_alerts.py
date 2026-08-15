"""public portal discovery proposals and manual LinkedIn alert links

Revision ID: c42df01a7e21
Revises: 8b7f9d2a6c10
Create Date: 2026-07-20 16:30:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c42df01a7e21"
down_revision: Union[str, None] = "8b7f9d2a6c10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "portal_discovery_proposals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_name", sa.String(length=300), nullable=False),
        sa.Column("careers_url", sa.String(length=2000), nullable=False),
        sa.Column("connector", sa.String(length=50), nullable=False),
        sa.Column("allowed_domains", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_data", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("careers_url", name="uq_portal_discovery_careers_url"),
    )
    op.create_index(
        "ix_portal_discovery_state_created",
        "portal_discovery_proposals",
        ["state", "created_at"],
        unique=False,
    )
    op.create_table(
        "linkedin_manual_alerts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("keywords", sa.JSON(), nullable=False),
        sa.Column("location", sa.String(length=300), nullable=False),
        sa.Column("search_url", sa.String(length=2000), nullable=False),
        sa.Column("cadence_days", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_linkedin_alert_enabled_created",
        "linkedin_manual_alerts",
        ["enabled", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_linkedin_alert_enabled_created", table_name="linkedin_manual_alerts")
    op.drop_table("linkedin_manual_alerts")
    op.drop_index("ix_portal_discovery_state_created", table_name="portal_discovery_proposals")
    op.drop_table("portal_discovery_proposals")
