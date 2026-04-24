"""
Run: `python -m chanakya.memory.smoke_test`

Creates a temporary SQLite DB and a temporary working directory with an embedded
Chroma persist directory. Saves one drawer and verifies semantic retrieval.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid

from chanakya.db import engine as engine_mod
from chanakya.memory.models import MemDiaryEntry, MemDrawer
from chanakya.memory.palace import create_room, initialize_wing
from chanakya.memory.store import get_agent_history, search_drawers, save_drawer


async def main() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["CHANAKYA_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    engine_mod.reset_engine()
    engine_mod.init_engine()

    from chanakya.db.engine import create_all_tables

    await create_all_tables()

    with tempfile.TemporaryDirectory() as wd:
        wing = await initialize_wing(wd)
        await create_room(wing, "authentication", "Auth flows, tokens, sessions")

        drawer = MemDrawer(
            wing=wing.name,
            room="authentication",
            session_id=uuid.uuid4(),
            task_id=uuid.uuid4(),
            agent_type="coding",
            agent_id="demo-agent",
            content=MemDiaryEntry(
                summary="Implemented login endpoint and added token validation.",
                decisions=["Use JWT for stateless auth."],
                assumptions=["Users already exist in DB."],
                dependencies=["FastAPI router wiring"],
                open_issues=["Refresh token rotation not implemented yet."],
                room="authentication",
            ),
        )

        save_drawer(wd, drawer, agent_id="demo-agent")

        hits = search_drawers(wd, query="token validation login", wing=wing.name, limit=5)
        assert hits, "expected at least one search hit"
        assert hits[0].room == "authentication"

        hist = get_agent_history(wd, wing=wing.name, agent_type="coding", limit=3)
        assert hist, "expected agent history"

    await engine_mod.get_engine().dispose()
    os.unlink(db_path)
    engine_mod.reset_engine()
    print("memory_smoke_test: OK")


if __name__ == "__main__":
    asyncio.run(main())

