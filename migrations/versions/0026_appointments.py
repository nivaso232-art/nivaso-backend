"""Add appointments table for appointment scheduling.

Introduces one new native Postgres enum type (``appointment_status``) and the
``appointments`` table: a per-business, per-customer scheduled booking,
optionally tied to a service, with a ``metadata`` JSONB bag for miscellaneous
non-schema data.

Unlike ``services``/``offers``/``coupons``, ``appointments`` is a static,
fixed-schema entity (like ``orders``/``customers``) and does not carry a
``custom_fields`` column.

This is a brand-new table with no existing data, so the downgrade is a real,
safe drop.

Revision ID: 0026
Revises: 0025
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


APPOINTMENT_STATUS_VALUES = (
    "scheduled",
    "confirmed",
    "completed",
    "cancelled",
    "no_show",
)


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM(*APPOINTMENT_STATUS_VALUES, name="appointment_status").create(
        bind, checkfirst=True
    )

    op.create_table(
        "appointments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "duration_minutes", sa.Integer(), server_default="30", nullable=False
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                *APPOINTMENT_STATUS_VALUES, name="appointment_status", create_type=False
            ),
            server_default="scheduled",
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}", nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_appointments"),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_appointments_business_id_businesses",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name="fk_appointments_customer_id_customers",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name="fk_appointments_service_id_services",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_appointments_business_id", "appointments", ["business_id"])
    op.create_index("ix_appointments_customer_id", "appointments", ["customer_id"])
    op.create_index(
        "ix_appointments_business_id_scheduled_at",
        "appointments",
        ["business_id", "scheduled_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_appointments_business_id_scheduled_at", table_name="appointments"
    )
    op.drop_index("ix_appointments_customer_id", table_name="appointments")
    op.drop_index("ix_appointments_business_id", table_name="appointments")
    op.drop_table("appointments")

    bind = op.get_bind()
    postgresql.ENUM(name="appointment_status").drop(bind, checkfirst=True)
