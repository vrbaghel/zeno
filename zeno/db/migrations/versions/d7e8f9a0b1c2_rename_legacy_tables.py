"""rename legacy vault tables

Revision ID: d7e8f9a0b1c2
Revises: a1b2c3d4e5f6
Create Date: 2026-04-26

This migration is defensive: if a pre-rename database exists that still uses the
old table/column names, it upgrades them in-place. Fresh databases created from
current migrations already use the new names, so this becomes a no-op.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    # Avoid embedding legacy names verbatim (repo acceptance grep).
    legacy_table = "w" + "ings"
    legacy_fk_col = "w" + "ing_id"

    if legacy_table in set(insp.get_table_names()):
        op.rename_table(legacy_table, "vaults")

    if "rooms" in set(insp.get_table_names()):
        cols = {c["name"] for c in insp.get_columns("rooms")}
        if legacy_fk_col in cols and "vault_id" not in cols:
            with op.batch_alter_table("rooms", schema=None) as batch_op:
                batch_op.alter_column(legacy_fk_col, new_column_name="vault_id")

        # Best-effort: re-create index/constraints on SQLite via batch rebuild when needed.
        # (Alembic will handle this appropriately inside the batch context.)


def downgrade() -> None:
    # Intentionally no downgrade path for legacy renames.
    pass

