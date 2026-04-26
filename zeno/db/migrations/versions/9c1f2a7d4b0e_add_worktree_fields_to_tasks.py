"""add worktree fields to tasks

Revision ID: 9c1f2a7d4b0e
Revises: f3d402b3bd47
Create Date: 2026-04-23

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c1f2a7d4b0e"
down_revision: Union[str, None] = "f3d402b3bd47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite: use batch copy-rebuild for ALTER.
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("worktree_path", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("branch_name", sa.String(length=255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("branch_name")
        batch_op.drop_column("worktree_path")

