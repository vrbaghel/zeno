"""drop agent provider/mode, add lead_session_id

Revision ID: 2c3d4e5f6a71
Revises: 1b2c3d4e5f70
Create Date: 2026-04-26

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "2c3d4e5f6a71"
down_revision = "1b2c3d4e5f70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Data reset by design for this migration.
    op.execute(sa.text("DELETE FROM agent_assignments"))
    op.execute(sa.text("DELETE FROM agents"))

    with op.batch_alter_table("sessions") as batch_op:
        batch_op.add_column(sa.Column("lead_session_id", sa.String(length=255), nullable=True))

    with op.batch_alter_table("agents") as batch_op:
        batch_op.drop_column("provider")
        batch_op.drop_column("mode")


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.add_column(
            sa.Column(
                "mode",
                sa.Enum("adapter", "api", name="agentmode", native_enum=False, length=32),
                nullable=False,
                server_default="adapter",
            )
        )
        batch_op.add_column(
            sa.Column(
                "provider",
                sa.Enum(
                    "gemini",
                    "anthropic",
                    "openai",
                    name="provider",
                    native_enum=False,
                    length=32,
                ),
                nullable=False,
                server_default="anthropic",
            )
        )

    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_column("lead_session_id")

