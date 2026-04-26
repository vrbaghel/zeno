"""agents table assignment refactor

Revision ID: f3d402b3bd47
Revises: b86554c1b399
Create Date: 2026-04-23 19:25:12.377863

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3d402b3bd47"
down_revision: Union[str, None] = "b86554c1b399"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
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
        ),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column(
            "provider",
            sa.Enum("gemini", "anthropic", "openai", name="provider", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column(
            "mode",
            sa.Enum("adapter", "api", name="agentmode", native_enum=False, length=32),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agents_name"), "agents", ["name"], unique=False)

    # SQLite: no ALTER for FK / DROP COLUMN — use batch copy-rebuild.
    # Existing assignment rows cannot be migrated without a target agent; clear for schema change.
    op.execute(sa.text("DELETE FROM agent_assignments"))

    with op.batch_alter_table("agent_assignments", schema=None) as batch_op:
        batch_op.add_column(sa.Column("agent_id", sa.Uuid(), nullable=False))
        batch_op.create_foreign_key(
            "fk_agent_assignments_agent_id",
            "agents",
            ["agent_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.drop_column("mode")
        batch_op.drop_column("agent_type")
        batch_op.drop_column("provider")


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM agent_assignments"))

    with op.batch_alter_table("agent_assignments", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "provider",
                sa.Enum("gemini", "anthropic", "openai", name="provider", native_enum=False, length=32),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "agent_type",
                sa.Enum(
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
        )
        batch_op.add_column(
            sa.Column(
                "mode",
                sa.Enum("adapter", "api", name="agentmode", native_enum=False, length=32),
                nullable=False,
            )
        )
        batch_op.drop_constraint("fk_agent_assignments_agent_id", type_="foreignkey")
        batch_op.drop_column("agent_id")

    op.drop_index(op.f("ix_agents_name"), table_name="agents")
    op.drop_table("agents")
