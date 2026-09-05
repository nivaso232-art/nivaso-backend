"""Super-admin entitlements and feature requests.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-02

``business_entitlements`` — one row per business, holds plan tier + overrides.
``feature_requests``       — access requests raised by client-admins.

Both use plain VARCHAR for status/plan (not pg enums) so adding new values
requires no ALTER TYPE migration.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── business_entitlements ─────────────────────────────────────────────────
    op.create_table(
        "business_entitlements",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan", sa.String(32), nullable=False, server_default="free"),
        sa.Column(
            "overrides",
            postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("granted_by", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_business_entitlements"),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_business_entitlements_business_id_businesses",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "business_id", name="uq_business_entitlements_business_id"
        ),
    )
    op.create_index(
        "ix_business_entitlements_business_id",
        "business_entitlements",
        ["business_id"],
    )

    # ── feature_requests ──────────────────────────────────────────────────────
    op.create_table(
        "feature_requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("feature", sa.String(128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("reviewed_by", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_feature_requests"),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_feature_requests_business_id_businesses",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_feature_requests_business_id", "feature_requests", ["business_id"]
    )
    op.create_index(
        "ix_feature_requests_status", "feature_requests", ["status"]
    )


def downgrade() -> None:
    op.drop_table("feature_requests")
    op.drop_table("business_entitlements")
