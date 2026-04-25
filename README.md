# Chanakya

Chanakya is a multi-agent orchestration framework.

Phase 1 implements a single CLI command, `chanakya`, that bootstraps configuration, resolves runtime mode, validates the mode, prints a startup summary, and exits.

## Phase 2 (Agents adapters)

Phase 2 adds the **Agents** adaptor layer: shared contracts (`AdaptorRequest`, `AdaptorResponse`, `AgentResponse`, metrics, errors) and a **Gemini** CLI adaptor that dispatches requests and returns structured results.

For a quick end-to-end check from the CLI (temporary test hook):

```bash
chanakya test-adaptor
chanakya test-adaptor --prompt "Your prompt here"
```

Programmatic usage (same stack the adaptor uses):

```python
import asyncio

from chanakya.agents.models import AdaptorMessage, AdaptorRequest, AdaptorRequestPayload
from chanakya.agents.registry import AdaptorRegistry


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
| **`sessions`** | Top-level run: user task submission, cwd, raw prompt, `ExecutionMode` (`yolo` \| `hitl`), lifecycle status. |
| **`execution_plans`** | One decomposition graph per plan revision; linked to a session. Revisions increment when a plan is revised. |
| **`tasks`** | Atomic work units (title, description, type, status, priority, optional parallel group, HITL checkpoint flag, result summary). |
| **`task_dependencies`** | Directed edges: `task_id` depends on `depends_on_task_id`. |
| **`agents`** | Registered agents: stable `name`, `type` (lead / coding / …), `system_prompt`, `provider`, `mode` (`adapter` \| `api`). |
| **`agent_assignments`** | Links a **task** to an **`agents`** row for one dispatch attempt (`started_at` / `completed_at`, assignment status). Provider/mode/type live on `agents`, not duplicated here. |
| **`task_metrics`** | Token and timing counts (and artifact counts) attached to a **completed** assignment row. |
| **`checkpoints`** | HITL checkpoint history (`presented` / `response` JSON payloads). |
| **`artifacts`** | File paths touched on disk, derived from adaptor artifact lists. |
| **`repository.py`** | All async DB operations in one module (`create_session`, `get_runnable_tasks`, …). |

### Agents and assignments

Register an agent once (`create_agent`), then attach work to it with **`create_assignment(task_id, session_id, agent_id)`**. Helpers include **`get_agent`**, **`get_agent_by_name`** (avoid duplicate names if you treat `name` as unique), and **`get_agent_with_assignments`** (eager-loads assignment history for resumption-style reads).

In code, `create_agent(..., agent_type=...)` maps to the SQL column **`type`** (Python `type` is reserved for the builtin).

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

Initial revision: **`b86554c1b399`** (full schema). Follow-up: **`f3d402b3bd47`** adds the **`agents`** table and refactors **`agent_assignments`** to reference **`agent_id`** (drops duplicated `agent_type` / `provider` / `mode` on assignments). That migration **deletes all rows in `agent_assignments`** before altering SQLite tables (no automatic backfill from old columns).

### Smoke test (repository + dependency logic)

Uses a **temporary** SQLite file (does not use your default `./.chanakya/chanakya.db`):

```bash
python -m chanakya.db.smoke_test
```

### Dependencies

Database-related runtime dependencies are declared in `pyproject.toml` (`sqlalchemy[asyncio]`, `aiosqlite`, `alembic`).

## Phase 4 (agent memory layer)

Phase 4 adds an embedded **ChromaDB** memory layer under `chanakya/memory/` that can persist and retrieve agent-authored context across sessions.

### Terminology

| Term | Meaning |
|------|---------|
| **`wing`** | A per-project namespace, derived from the working directory name (slugified) and stored in SQLite (`wings` table). |
| **`room`** | A lead-agent-defined topic area under a wing (e.g. `authentication`, `frontend`), stored in SQLite (`rooms` table). |
| **`drawer`** | One semantic-searchable entry stored in ChromaDB (typically one per completed task). |
| **`diary_entry`** | A structured briefing authored by an agent after task completion; stored as the drawer document. |
| **`MemContext`** | Assembled context (session summary + relevant drawers + agent history) suitable for prompt injection. |
| **`agent_id`** | The agent instance identifier stored in drawer metadata, used to scope history retrieval to a specific agent instance when desired. |

### Persistence

- **SQLite**: wings/rooms are stored in the relational DB.
- **ChromaDB**: embedded local persistence under:
  - **`<cwd>/.chanakya/memory/chroma/`**
- **Collections**: one collection per wing, named:
  - **`chanakya_<wingSlug>`**

### Adapter contract changes

`AgentResponse` now supports an optional `diary_entry` block. The Gemini adapter prompts agents to emit it and logs a warning if it is missing (missing diary entries are recoverable).

### Smoke test (memory)

Runs against a temporary SQLite file and a temporary working directory; saves one drawer and verifies retrieval:

```bash
python -m chanakya.memory.smoke_test
```

### Retrieval notes

- **`search_drawers(..., room=None)`**: `room` is optional and only applied as a filter when explicitly provided.
- **`get_agent_history(...)`**: history can be scoped broadly (by `agent_type`) or narrowly (by `agent_id`), and always includes the `wing` filter.

### Dependencies

Memory-layer runtime dependencies are declared in `pyproject.toml` (notably `chromadb`).

## Phase 5 (lead agent contracts)

Phase 5 formalizes the **lead agent request/response** contracts and adds a persisted orchestrator state enum. No orchestrator execution logic is implemented in this phase—only the models and validation that later phases will build on.

### Contracts

Defined in `chanakya/agents/models.py`:

- **Clarification flow**:
  - `ClarificationQuestion`
  - `ClarificationResponse` (`type="clarification"`)
- **Execution planning**:
  - `RoomDefinition`
  - `TaskDefinition`
  - `ExecutionPlanResponse` (`type="execution_plan"`, includes required `diary_entry`)
- **Lead agent request**:
  - `LeadAgentRequest` (includes `memory_context` as a lightweight `AgentContext` mirror model; the orchestrator converts `MemContext → AgentContext`)
- **Validation**:
  - `validate_lead_response(...) -> list[str]` enforces discriminator correctness, dependency/room consistency, parallel group format, required diary entry, and rejects clarification responses in YOLO mode.

### Orchestrator state (persisted)

- **Enum**: `chanakya/core/enums.py` defines `OrchestratorState` (shared primitive).
- **DB**: `sessions.orchestrator_state` is stored in SQLite with default `INITIALIZING`.
- **Repository helpers**:
  - `update_orchestrator_state(session_id, state)`
  - `get_orchestrator_state(session_id)`

### Smoke test (contracts)

```bash
./.venv/bin/python -m compileall -q chanakya
```

## Phase 8A (lead agent foundation)

Phase 8A introduces the lead agent prompt architecture and a unified lead response schema (clarification/execution/terminate), plus a persistent lead-agent subprocess adapter under `chanakya/agents/lead/`.

## Phase 8B (lead agent ↔ orchestrator integration)

Phase 8B wires the lead agent into the orchestrator: the lead agent produces an execution plan, the orchestrator persists it to SQLite (rooms/tasks/dependencies/assignments), supports a HITL clarification loop and plan approval, and routes worker dispatch by provider.

## Phase 6 (bare orchestrator)

Phase 6 introduces the first **end-to-end orchestrator** under `chanakya/orchestrator/`. It is intentionally minimal: one session, one task, one agent, one worktree, one merge.

### What it does

- Initializes the session (creates `./.chanakya/`, ensures git, initializes the memory wing, creates SQLite session).
- Creates a git worktree under `./.chanakya/worktrees/<session_id>/<task_id>` and a branch `chanakya/<session_id>/<task_id>`.
- Dispatches a single “test agent” via the Gemini adaptor, saves metrics + artifacts to SQLite, and saves a memory drawer to ChromaDB.
- Merges the worktree branch back to the current branch, then cleans up the worktree and branch.
- Marks the session as completed (or failed on first error).

### Smoke test (orchestrator)

Run from the repository root, using the repo-local venv:

```bash
./.venv/bin/python -m chanakya.orchestrator.smoke_phase6
```

Optional settings:

- `ORCHESTRATOR_TIMEOUT_SECONDS`: overrides adaptor dispatch timeout for the orchestrator (default: 60).

## Phase 7 (CLI and orchestrator)

The `chanakya` command starts an **interactive** session: one long-lived `OrchestratorCore`, a new SQLite `DbSession` per task line you enter, slash commands (`/quit`, `/status`, `/help`), and Rich line-based progress. Default execution mode is HITL; pass `--yolo` for YOLO.

```bash
chanakya
chanakya --yolo
```

The Phase 2 adaptor smoke hook remains: `chanakya test-adaptor`.

