# Chanakya

Chanakya is a multi-agent orchestration framework.

Phase 1 implements a single CLI command, `chanakya`, that bootstraps configuration, resolves runtime mode, validates the mode, prints a startup summary, and exits.

## Phase 2 (Arthashastra adapters)

Phase 2 adds the **Arthashastra** adaptor layer: shared contracts (`AdaptorRequest`, `AdaptorResponse`, `AgentResponse`, metrics, errors) and a **Gemini** CLI adaptor that dispatches requests and returns structured results.

For a quick end-to-end check from the CLI (temporary test hook):

```bash
chanakya test-adaptor
chanakya test-adaptor --prompt "Your prompt here"
```

Programmatic usage (same stack the adaptor uses):

```python
import asyncio

from chanakya.arthashastra.models import AdaptorMessage, AdaptorRequest, AdaptorRequestPayload
from chanakya.arthashastra.registry import AdaptorRegistry


async def main():
    registry = AdaptorRegistry.discover()
    adaptor = registry.default()

    req = AdaptorRequest(
        agent_id="demo-agent",
        payload=AdaptorRequestPayload(
            system="You are a helpful assistant.",
            messages=[AdaptorMessage(role="user", content="Say hello.")],
        ),
    )

    result = await adaptor.dispatch(req)
    print(result)


asyncio.run(main())
```

## Phase 3 (relational database layer)

Phase 3 adds a **SQLite** persistence layer under `chanakya/db/`, exposed through **SQLAlchemy 2.x** (async) and **Alembic** migrations. This layer is passive: it stores and retrieves orchestration state; it does not run agents or make orchestration decisions.

### Terminology

| Term | Meaning |
|------|---------|
| **`chanakya/db/`** | Database package: engine, ORM models, repository, migrations. |
| **`Db*` models** | SQLAlchemy ORM classes (`DbSession`, `DbTask`, …) — the `Db` prefix marks **persistence-layer** types so they are not confused with domain/orchestrator types later. |
| **`sessions`** | Top-level run: user task submission, cwd, raw prompt, mode (`yolo` \| `hitl`), lifecycle status. |
| **`execution_plans`** | One decomposition graph per plan revision; linked to a session. Revisions increment when a plan is revised. |
| **`tasks`** | Atomic work units (title, description, type, status, priority, optional parallel group, HITL checkpoint flag, result summary). |
| **`task_dependencies`** | Directed edges: `task_id` depends on `depends_on_task_id`. |
| **`agent_assignments`** | Which agent run handled a task (supports retries / different agents over time). |
| **`task_metrics`** | Token and timing counts (and artifact counts) attached to a **completed** assignment row. |
| **`checkpoints`** | HITL checkpoint history (`presented` / `response` JSON payloads). |
| **`artifacts`** | File paths touched on disk, derived from adaptor artifact lists. |
| **`repository.py`** | All async DB operations in one module (`create_session`, `get_runnable_tasks`, …). |

### Default database location

If **`CHANAKYA_DATABASE_URL`** is not set, Chanakya uses a file under the **process current working directory**:

- **`<cwd>/.chanakya/chanakya.db`**

The `.chanakya/` directory is created as needed. That directory is listed in `.gitignore` so local databases are not committed.

Phase 1 **CLI user config** still lives under the home directory (`~/.chanakya/config.toml`); only the **default SQLite file** for Phase 3 is cwd-relative unless you override it with `CHANAKYA_DATABASE_URL`.

Override explicitly when you want a different file or path:

```bash
export CHANAKYA_DATABASE_URL="sqlite+aiosqlite:////absolute/path/to/chanakya.db"
```

### Migrations (Alembic)

From the **repository root** (where `alembic.ini` lives), after setting `CHANAKYA_DATABASE_URL` if you are not using the default:

```bash
alembic upgrade head
```

Revision scripts live under `chanakya/db/migrations/versions/`.

### Smoke test (repository + dependency logic)

Uses a **temporary** SQLite file (does not use your default `./.chanakya/chanakya.db`):

```bash
python -m chanakya.db.smoke_test
```

### Dependencies

Database-related runtime dependencies are declared in `pyproject.toml` (`sqlalchemy[asyncio]`, `aiosqlite`, `alembic`).

