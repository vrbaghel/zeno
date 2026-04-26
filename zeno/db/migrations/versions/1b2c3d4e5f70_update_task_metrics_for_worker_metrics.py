"""update task_metrics for worker metrics

Revision ID: 1b2c3d4e5f70
Revises: 9c1f2a7d4b0e
Create Date: 2026-04-25

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1b2c3d4e5f70"
down_revision = "9c1f2a7d4b0e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("task_metrics") as batch_op:
        batch_op.drop_column("tokens_estimated")
        batch_op.drop_column("token_deviation")

        batch_op.add_column(sa.Column("cache_read_tokens", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("cache_creation_tokens", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("cost_usd", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("num_turns", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("model", sa.String(length=255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("task_metrics") as batch_op:
        batch_op.drop_column("model")
        batch_op.drop_column("num_turns")
        batch_op.drop_column("cost_usd")
        batch_op.drop_column("cache_creation_tokens")
        batch_op.drop_column("cache_read_tokens")

        batch_op.add_column(sa.Column("token_deviation", sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column(
                "tokens_estimated",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )
