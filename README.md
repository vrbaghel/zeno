# Zeno

Zeno is a multi-agent orchestration framework with:

- an interactive CLI (`zeno`)
- a lead agent that produces an execution plan
- worker agents that execute tasks in isolated git worktrees
- persistence via SQLite (orchestrator state) and ChromaDB (semantic memory)

## Install (dev)

From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
```

## Run

```bash
zeno          # HITL mode (default)
zeno --yolo   # YOLO mode
```

The `zeno` command starts an **interactive** session:

- One long-lived `OrchestratorCore`
- A new SQLite `DbSession` per task line you enter
- Slash commands: `/quit`, `/status`, `/help`

## Core terminology

| Term | Meaning |
|------|---------|
| **`vault`** | Per-project namespace, derived from the working directory name (slugified) and stored in SQLite (`vaults` table). |
| **`room`** | A lead-defined topic area under a vault (e.g. `auth`, `frontend`), stored in SQLite (`rooms` table). |
| **`trace`** | One semantic-searchable entry stored in ChromaDB (typically one per completed task). |
| **`log`** | A structured briefing authored by an agent after task completion; stored as the trace document. |
| **`session`** | One user-submitted task line persisted to SQLite (`sessions` table). |
| **`execution_plan`** | A plan revision produced by the lead agent, persisted to SQLite (`execution_plans`, `tasks`, `task_dependencies`). |

## Persistence

- **SQLite (orchestrator state)**: default path is **`<cwd>/.zeno/zeno.db`** (override via `ZENO_DATABASE_URL`).
- **ChromaDB (memory)**: local persistence under **`<cwd>/.zeno/mind/chroma/`**, one collection per vault named **`zeno_<vaultSlug>`**.

## Database layer (SQLite)

Zeno stores orchestration state under `zeno/db/` using **SQLAlchemy 2.x (async)**.

### Terminology

| Term | Meaning |
|------|---------|
| **`zeno/db/`** | Database package: engine, ORM models, repository, migrations. |
| **`Db*` models** | SQLAlchemy ORM classes (`DbSession`, `DbTask`, …) — the `Db` prefix marks **persistence-layer** types so they are not confused with domain/orchestrator types later. |
| **`sessions`** | Top-level run: user task submission, cwd, raw prompt, `ExecutionMode` (`yolo` \| `hitl`), lifecycle status. |
| **`execution_plans`** | One decomposition graph per plan revision; linked to a session. Revisions increment when a plan is revised. |
| **`tasks`** | Atomic work units (title, description, type, status, priority, optional parallel group, HITL checkpoint flag, result summary). |
| **`task_dependencies`** | Directed edges: `task_id` depends on `depends_on_task_id`. |
| **`agents`** | Registered agents: stable `name`, `type` (lead / coding / …), `system_prompt`. |
| **`agent_assignments`** | Links a **task** to an **`agents`** row for one dispatch attempt (`started_at` / `completed_at`, assignment status). Provider/mode/type live on `agents`, not duplicated here. |
| **`task_metrics`** | Token and timing counts (and artifact counts) attached to a **completed** assignment row. |
| **`checkpoints`** | HITL checkpoint history (`presented` / `response` JSON payloads). |
| **`artifacts`** | File paths touched on disk, derived from agent artifact lists. |
| **`repository.py`** | All async DB operations in one module (`create_session`, `get_runnable_tasks`, …). |

### Agents and assignments

Register an agent once (`create_agent`), then attach work to it with **`create_assignment(task_id, session_id, agent_id)`**. Helpers include **`get_agent`**, **`get_agent_by_name`** (avoid duplicate names if you treat `name` as unique), and **`get_agent_with_assignments`** (eager-loads assignment history for resumption-style reads).

In code, `create_agent(..., agent_type=...)` maps to the SQL column **`type`** (Python `type` is reserved for the builtin).

### Default database location

If **`ZENO_DATABASE_URL`** is not set, Zeno uses a file under the **process current working directory**:

- **`<cwd>/.zeno/zeno.db`**

The `.zeno/` directory is created as needed. That directory is listed in `.gitignore` so local databases are not committed.

Phase 1 **CLI user config** still lives under the home directory (`~/.zeno/config.toml`); only the **default SQLite file** for Phase 3 is cwd-relative unless you override it with `ZENO_DATABASE_URL`.

Override explicitly when you want a different file or path:

```bash
export ZENO_DATABASE_URL="sqlite+aiosqlite:////absolute/path/to/zeno.db"
```

### Migrations (Alembic)

From the **repository root** (where `alembic.ini` lives), after setting `ZENO_DATABASE_URL` if you are not using the default:

```bash
alembic upgrade head
```

Revision scripts live under `zeno/db/migrations/versions/`.

Initial revision: **`b86554c1b399`** (full schema). Follow-up: **`f3d402b3bd47`** adds the **`agents`** table and refactors **`agent_assignments`** to reference **`agent_id`** (drops duplicated `agent_type` / `provider` / `mode` on assignments). That migration **deletes all rows in `agent_assignments`** before altering SQLite tables (no automatic backfill from old columns).

### Smoke test (repository + dependency logic)

Uses a **temporary** SQLite file (does not use your default `./.zeno/zeno.db`):

```bash
python -m zeno.db.smoke_test
```

### Dependencies

Database-related runtime dependencies are declared in `pyproject.toml` (`sqlalchemy[asyncio]`, `aiosqlite`, `alembic`).

## Phase 4 (agent memory layer)

Phase 4 adds an embedded **ChromaDB** memory layer under `zeno/memory/` that can persist and retrieve agent-authored context across sessions.

### Persistence

- **SQLite**: vaults/rooms are stored in the relational DB.
- **ChromaDB**: embedded local persistence under:
  - **`<cwd>/.zeno/mind/chroma/`**
- **Collections**: one collection per vault, named:
  - **`zeno_<vaultSlug>`**

### Adapter contract changes

Agent responses support an optional `log` block. Zeno stores this as a memory `trace` document after task completion.

### Smoke test (memory)

Runs against a temporary SQLite file and a temporary working directory; saves one trace and verifies retrieval:

```bash
python -m zeno.memory.smoke_test
```

### Retrieval notes

- **`search_traces(..., room=None)`**: `room` is optional and only applied as a filter when explicitly provided.
- **`get_agent_logs(...)`**: history can be scoped broadly (by `agent_type`) or narrowly (by `agent_id`), and always includes the `vault` filter.

### Dependencies

Memory-layer runtime dependencies are declared in `pyproject.toml` (notably `chromadb`).

## Phase 5 (lead agent contracts)

Phase 5 formalizes the **lead agent request/response** contracts and adds a persisted orchestrator state enum. No orchestrator execution logic is implemented in this phase—only the models and validation that later phases will build on.

### Contracts

Defined in `zeno/agents/models.py`:

- **Clarification flow**:
  - `ClarificationQuestion`
  - `ClarificationResponse` (`type="clarification"`)
- **Execution planning**:
  - `RoomDefinition`
  - `TaskDefinition`
  - `ExecutionPlanResponse` (`type="execution_plan"`, includes required `log`)
- **Lead agent request**:
  - `LeadAgentRequest` (includes `memory_context` as a lightweight `AgentContext` mirror model; the orchestrator converts `MemContext → AgentContext`)
- **Validation**:
  - `validate_lead_response(...) -> list[str]` enforces discriminator correctness, dependency/room consistency, parallel group format, required log, and rejects clarification responses in YOLO mode.

### Orchestrator state (persisted)

- **Enum**: `zeno/core/enums.py` defines `OrchestratorState` (shared primitive).
- **DB**: `sessions.orchestrator_state` is stored in SQLite with default `INITIALIZING`.
- **Repository helpers**:
  - `update_orchestrator_state(session_id, state)`
  - `get_orchestrator_state(session_id)`

### Smoke test (contracts)

```bash
./.venv/bin/python -m compileall -q zeno
```

## Phase 8A (lead agent foundation)

Phase 8A introduces the lead agent prompt architecture and a unified lead response schema (clarification/execution/terminate), plus a persistent lead-agent subprocess adapter under `zeno/agents/lead/`.

## Phase 8B (lead agent ↔ orchestrator integration)

Phase 8B wires the lead agent into the orchestrator: the lead agent produces an execution plan, the orchestrator persists it to SQLite (rooms/tasks/dependencies/assignments), supports a HITL clarification loop and plan approval, and routes worker dispatch by provider.

## Phase 6 (bare orchestrator)

Phase 6 introduces the first **end-to-end orchestrator** under `zeno/orchestrator/`. It is intentionally minimal: one session, one task, one agent, one worktree, one merge.

### What it does

- Initializes the session (creates `./.zeno/`, ensures git, initializes the memory vault, creates SQLite session).
- Ensures the repo has at least one commit (creates an empty initial commit if needed) so worktree branches can merge back cleanly.
- Creates a git worktree under `./.zeno/worktrees/<session_id>/<task_id>` and a branch `zeno/<session_id>/<task_id>`.
- Dispatches a worker agent, saves metrics + artifacts to SQLite, and saves a memory trace to ChromaDB.
- Stages + commits any worker changes inside the worktree, then merges the worktree branch back to the current branch, then cleans up the worktree and branch.
- Marks the session as completed (or failed on first error).

Worker responses support two outcomes via a `type` discriminator:

- `type: success`: normal completion (summary, artifacts, log)
- `type: terminate`: the worker could not complete the task; the orchestrator marks the task failed and cleans up the worktree.

### Smoke test (orchestrator)

Run from the repository root, using the repo-local venv:

```bash
./.venv/bin/python -m zeno.orchestrator.smoke_phase6
```

Optional settings:

- `ORCHESTRATOR_TIMEOUT_SECONDS`: overrides agent dispatch timeout for the orchestrator (default: 60).

## Phase 7 (CLI and orchestrator)

The `zeno` command starts an **interactive** session: one long-lived `OrchestratorCore`, a new SQLite `DbSession` per task line you enter, slash commands (`/quit`, `/status`, `/help`), and Rich line-based progress. Default execution mode is HITL; pass `--yolo` for YOLO.

```bash
zeno
zeno --yolo
```

The CLI is intended to be the primary entrypoint.

