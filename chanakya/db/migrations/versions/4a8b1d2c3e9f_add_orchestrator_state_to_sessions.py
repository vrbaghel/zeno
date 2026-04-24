"""add orchestrator_state to sessions

Revision ID: 4a8b1d2c3e9f
Revises: 2f6d1a4b8c0d
Create Date: 2026-04-23

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4a8b1d2c3e9f"
down_revision: Union[str, None] = "2f6d1a4b8c0d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "orchestrator_state",
                sa.Enum(
                    "INITIALIZING",
                    "AWAITING_LEAD",
                    "AWAITING_HUMAN",
                    "PLANNING",
                    "EXECUTING",
                    "MERGING",
                    "COMPLETED",
                    "FAILED",
                    "ABORTED",
                    name="orchestratorstate",
                    native_enum=False,
                    length=32,
                ),
                nullable=False,
                server_default="INITIALIZING",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.drop_column("orchestrator_state")

