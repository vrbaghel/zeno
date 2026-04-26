"""
Run: `python -m zeno.db.smoke_test`

Uses a temporary SQLite file; does not touch the default cwd-based database (./.zeno/zeno.db).
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid

from zeno.db import engine as engine_mod
from zeno.core.enums import ExecutionMode
from zeno.db.models import TaskType
from zeno.db.repository import (
    add_task_dependency,
    create_execution_plan,
    create_session,
    create_task,
    get_runnable_tasks,
    get_tasks_by_plan,
)


async def main() -> None:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["ZENO_DATABASE_URL"] = f"sqlite+aiosqlite:///{path}"
    engine_mod.reset_engine()
    engine_mod.init_engine()

    from zeno.db.engine import create_all_tables

    await create_all_tables()

    sid = uuid.uuid4()
    s = await create_session(
        ExecutionMode.YOLO,
        working_directory="/tmp",
        raw_input="smoke test",
        id=sid,
    )
    assert s.id == sid

    plan = await create_execution_plan(sid)
    t1 = await create_task(
        plan.id,
        sid,
        "A",
        "root",
        TaskType.foundational,
        priority=1,
    )
    t2 = await create_task(
        plan.id,
        sid,
        "B",
        "depends on A",
        TaskType.implementation,
        priority=2,
    )
    t3 = await create_task(
        plan.id,
        sid,
        "C",
        "depends on B",
        TaskType.validation,
        priority=3,
    )
    await add_task_dependency(t2.id, t1.id)
    await add_task_dependency(t3.id, t2.id)

    run0 = await get_runnable_tasks(plan.id)
    assert len(run0) == 1 and run0[0].id == t1.id

    from zeno.db.repository import complete_task

    await complete_task(t1.id, "done A")

    run1 = await get_runnable_tasks(plan.id)
    assert len(run1) == 1 and run1[0].id == t2.id

    await complete_task(t2.id, "done B")

    run2 = await get_runnable_tasks(plan.id)
    assert len(run2) == 1 and run2[0].id == t3.id

    all_tasks = await get_tasks_by_plan(plan.id)
    assert len(all_tasks) == 3

    await engine_mod.get_engine().dispose()
    os.unlink(path)
    engine_mod.reset_engine()
    print("smoke_test: OK")


if __name__ == "__main__":
    asyncio.run(main())
