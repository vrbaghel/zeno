"""agents.type free-form string

Revision ID: 3a5b7c9d1e0f
Revises: 2c3d4e5f6a71
Create Date: 2026-04-26

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "3a5b7c9d1e0f"
down_revision = "2c3d4e5f6a71"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.alter_column(
            "type",
            existing_type=sa.Enum(
                "lead",
                "coding",
                "testing",
                "requirements",
                "integration",
                "other",
                name="agenttype",
                native_enum=False,
                length=32,
            ),
            type_=sa.String(length=64),
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.alter_column(
            "type",
            existing_type=sa.String(length=64),
            type_=sa.Enum(
                "lead",
                "coding",
                "testing",
                "requirements",
                "integration",
                "other",
                name="agenttype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        )
