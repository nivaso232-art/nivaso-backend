"""Drop module_requests — consolidated onto the existing FeatureRequest system.

``ModuleRequest`` duplicated ``FeatureRequest`` (client submits a flag key,
super-admin reviews, approval writes to overrides) almost exactly, minus the
generality. Signup now creates ``FeatureRequest`` rows instead. No production
data depended on this table (only this session's own test businesses, which
have already been deleted), so a straight drop is safe.

Revision ID: 0030
Revises: 0029
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("module_requests")
    bind = op.get_bind()
    postgresql.ENUM(name="module_request_status").drop(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM("pending", "approved", "denied", name="module_request_status").create(
        bind, checkfirst=True
    )
    op.create_table(
        "module_requests",
        sa.Column("module_key", sa.String(64), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM("pending", "approved", "denied", name="module_request_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.String(128)),
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_module_requests"),
        sa.UniqueConstraint("business_id", "module_key", name="uq_module_requests_business_id_module_key"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], name="fk_module_requests_business_id_businesses", ondelete="CASCADE"),
    )
    op.create_index("ix_module_requests_business_id", "module_requests", ["business_id"])
    op.create_index("ix_module_requests_status", "module_requests", ["status"])
