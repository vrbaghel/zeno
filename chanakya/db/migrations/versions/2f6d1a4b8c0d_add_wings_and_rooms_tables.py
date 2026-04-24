"""add wings and rooms tables

Revision ID: 2f6d1a4b8c0d
Revises: 9c1f2a7d4b0e
Create Date: 2026-04-23

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2f6d1a4b8c0d"
down_revision: Union[str, None] = "9c1f2a7d4b0e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "wings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("path"),
    )

    op.create_table(
        "rooms",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("wing_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["wing_id"], ["wings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("wing_id", "name", name="uq_wing_room_name"),
    )
    op.create_index(op.f("ix_rooms_wing_id"), "rooms", ["wing_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_rooms_wing_id"), table_name="rooms")
    op.drop_table("rooms")
    op.drop_table("wings")

