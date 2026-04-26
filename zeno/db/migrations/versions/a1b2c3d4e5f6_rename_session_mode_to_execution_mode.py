"""rename sessions.mode enum to executionmode

Revision ID: a1b2c3d4e5f6
Revises: 4a8b1d2c3e9f
Create Date: 2026-04-24

SQLite stores string-backed enums as VARCHAR; values stay yolo|hitl.
This migration updates the declared enum name for schema consistency.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "4a8b1d2c3e9f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.alter_column(
            "mode",
            existing_type=sa.Enum("yolo", "hitl", name="sessionmode", native_enum=False, length=16),
            type_=sa.Enum("yolo", "hitl", name="executionmode", native_enum=False, length=16),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.alter_column(
            "mode",
            existing_type=sa.Enum("yolo", "hitl", name="executionmode", native_enum=False, length=16),
            type_=sa.Enum("yolo", "hitl", name="sessionmode", native_enum=False, length=16),
            existing_nullable=False,
        )
