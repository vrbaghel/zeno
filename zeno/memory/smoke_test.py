"""
Run: `python -m zeno.memory.smoke_test`

Creates a temporary SQLite DB and a temporary working directory with an embedded
Chroma persist directory. Saves one trace and verifies semantic retrieval.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid

from zeno.db import engine as engine_mod
from zeno.memory.models import MemLog, MemTrace
from zeno.memory.mind import create_room, initialize_vault
from zeno.memory.store import get_agent_logs, search_traces, save_trace


async def main() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["ZENO_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    engine_mod.reset_engine()
    engine_mod.init_engine()

    from zeno.db.engine import create_all_tables

    await create_all_tables()

    with tempfile.TemporaryDirectory() as wd:
        vault = await initialize_vault(wd)
        await create_room(vault, "authentication", "Auth flows, tokens, sessions")

        trace = MemTrace(
            vault=vault.name,
            room="authentication",
            session_id=uuid.uuid4(),
            task_id=uuid.uuid4(),
            agent_type="coding",
            agent_id="demo-agent",
            content=MemLog(
                summary="Implemented login endpoint and added token validation.",
                decisions=["Use JWT for stateless auth."],
                assumptions=["Users already exist in DB."],
                dependencies=["FastAPI router wiring"],
                open_issues=["Refresh token rotation not implemented yet."],
                room="authentication",
            ),
        )

        save_trace(wd, trace, agent_id="demo-agent")

        hits = search_traces(wd, query="token validation login", vault=vault.name, limit=5)
        assert hits, "expected at least one search hit"
        assert hits[0].room == "authentication"

        hist = get_agent_logs(wd, vault=vault.name, agent_type="coding", limit=3)
        assert hist, "expected agent history"

    await engine_mod.get_engine().dispose()
    os.unlink(db_path)
    engine_mod.reset_engine()
    print("memory_smoke_test: OK")


if __name__ == "__main__":
    asyncio.run(main())

