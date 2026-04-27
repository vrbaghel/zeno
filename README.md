# Zeno

Zeno is a multi-agent orchestration framework built on the Claude Agent SDK. It coordinates a **lead agent** that plans work and **worker agents** that execute tasks in isolated git worktrees, with full persistence and semantic memory across sessions.

## Features

- **Interactive CLI** (`zeno`) with HITL and YOLO execution modes
- **Lead agent** that clarifies ambiguous requests, produces a structured execution plan, and continues planning in chunks as work progresses
- **Worker agents** dispatched per-task into isolated git worktrees; changes are committed and merged back automatically
- **Chunked (lazy) planning** — the lead emits one logical phase at a time; the orchestrator prefetches the next chunk in the background while the current phase runs
- **Parallel execution** — tasks in the same `parallel_group` run concurrently (up to five tasks per group)
- **HITL checkpoints** — human approval gates before expensive or risky tasks; plan revision and cancellation supported at every chunk boundary
- **Session resume** — orchestrator state is fully persisted so sessions can be resumed across restarts
- **Semantic memory** via ChromaDB — agents write structured diary entries after each task; future tasks receive relevant context automatically
- **Worker parse-error reconciliation** — on a bad structured response, the worker adapter retries with a cheap correction prompt before failing

## Install (dev)

From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

Or with `uv`:

```bash
uv sync
source .venv/bin/activate
```

## Run

```bash
zeno           # HITL mode (default)
zeno --yolo    # YOLO mode — no approval gates
```

Slash commands available at the prompt:

| Command | Description |
|---------|-------------|
| `/quit` | Exit the session |
| `/status` | Show current session and task state |
| `/help` | List available commands |

## Architecture

```
CLI (zeno)
  └─ OrchestratorCore
       ├─ LeadAgentAdapter     ← long-lived SDK session; produces chunked execution plans
       ├─ WorkerAdapter        ← one-shot SDK dispatch per task; runs in git worktree
       ├─ ExecutionPlanner     ← persists plans/rooms/tasks/deps/assignments to SQLite
       ├─ git ops              ← worktree create, commit, merge, cleanup
       └─ memory (ChromaDB)    ← semantic trace store; context injected into each worker
```

### Lead agent

The lead agent runs as a persistent SDK session. It:

1. Asks clarifying questions (HITL mode only) via `AskUserQuestion`
2. Emits an `ExecutionPlanResponse` (with `is_final` flag for chunked planning)
3. Receives `EXECUTION UPDATE` messages when asked for the next chunk
4. Can be asked to revise a plan mid-execution (e.g. after a HITL rejection)

Prompt layers live under `zeno/agents/lead/prompts/layers/` and are composed dynamically by `zeno/agents/lead/composer.py`.

### Worker agents

Each task gets its own SDK `query()` call with:

- A dynamically built system prompt (role derived from `agent_type`, responsibilities from the plan)
- ChromaDB context from prior tasks injected into the prompt
- A structured JSON output schema (`WORKER_RESPONSE_SCHEMA`)
- Tool access scoped to the task type (`testing`/`validation` agents get `Bash`; others get file-write tools only)

Worker responses are either `WorkerResponse` (success) or `WorkerTerminateResponse` (task failed).

### Execution flow

```
User prompt
  → OrchestratorCore.run()
  → Lead agent clarifies (HITL) + emits first plan chunk
  → Planner persists rooms / tasks / assignments to SQLite
  → HITL plan-approval checkpoint (if HITL mode)
  → For each runnable task (respecting dependencies and parallel groups):
      → Fetch ChromaDB context
      → Create git worktree on branch zeno/<session_id>/<task_id>
      → WorkerAdapter.dispatch() → SDK query
      → Commit worker changes in worktree
      → Merge worktree branch back to base branch
      → Save diary entry to ChromaDB; save metrics + artifacts to SQLite
      → Mark task complete
  → At each chunk boundary:
      → HITL checkpoint: approve / revise / cancel
      → If approved: lead.continue_plan() → next chunk appended to active plan
  → Session marked COMPLETED
  → Input loop returns; user can submit next prompt or /quit
```

## Core concepts

| Term | Meaning |
|------|---------|
| **vault** | Per-project namespace derived from the working directory name (slugified), stored in SQLite. |
| **room** | A lead-defined topic area under a vault (e.g. `backend`, `frontend`), stored in SQLite. |
| **trace** | One semantic-searchable entry in ChromaDB — typically one diary entry per completed task. |
| **log** | Structured agent diary entry (`summary`, `decisions`, `assumptions`, `open_issues`, `room`); stored as the trace document. |
| **session** | One user-submitted task line persisted to SQLite (`sessions` table). |
| **execution_plan** | A plan revision produced by the lead agent, persisted across `execution_plans`, `tasks`, and `task_dependencies` tables. |
| **chunk** | One `ExecutionPlanResponse` emitted by the lead; `is_final: false` means more chunks follow. |
| **parallel_group** | A single uppercase letter (`A`–`Z`) shared by tasks that can run concurrently. |
| **checkpoint** | A HITL gate persisted to the `checkpoints` table; supports approve / revise / cancel. |

## Persistence

| Store | Path | Purpose |
|-------|------|---------|
| SQLite | `<cwd>/.zeno/zeno.db` | Orchestrator state: sessions, plans, tasks, agents, assignments, metrics, checkpoints, artifacts |
| ChromaDB | `<cwd>/.zeno/mind/chroma/` | Semantic memory: one collection per vault (`zeno_<vaultSlug>`) |
| Config | `~/.zeno/config.toml` | CLI user config (model, API keys, defaults) |

Override the SQLite path:

```bash
export ZENO_DATABASE_URL="sqlite+aiosqlite:////absolute/path/to/zeno.db"
```

## Database migrations

From the repo root:

```bash
alembic upgrade head
```

Migration scripts live under `zeno/db/migrations/versions/`.

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ZENO_DATABASE_URL` | `<cwd>/.zeno/zeno.db` | SQLite connection string |
| `ORCHESTRATOR_TIMEOUT_SECONDS` | `60` | Per-task agent dispatch timeout |
| `ZENO_LEAD_RESUME_SEND_PROMPT` | `""` | Set to `1`/`true` to re-send the system prompt on lead session resume |

## Smoke tests

```bash
# Database layer
python -m zeno.db.smoke_test

# Memory layer
python -m zeno.memory.smoke_test

# Bare orchestrator (Phase 6)
python -m zeno.orchestrator.smoke_phase6

# Compile check
python -m compileall -q zeno
```

## Project layout

```
zeno/
  agents/
    lead/           ← lead agent adapter, composer, prompt layers
    worker/         ← worker agent adapter, composer
    models.py       ← shared request/response contracts and JSON schemas
  cli/              ← Typer CLI, Rich display, input handling
  core/             ← config, enums (ExecutionMode, OrchestratorState, LeadAgentStage)
  db/               ← SQLAlchemy 2.x async engine, ORM models, repository, Alembic migrations
  memory/           ← ChromaDB store, retrieval, MemLog/MemTrace/MemVault models
  orchestrator/
    core.py         ← OrchestratorCore: main run loop, parallel dispatch, HITL, chunked planning
    planner.py      ← ExecutionPlanner: persists plans/rooms/tasks to SQLite
    session.py      ← session init, workspace prep, teardown
    git.py          ← worktree create/commit/merge/cleanup helpers
    errors.py       ← typed exception hierarchy
```

## License

See [LICENSE](LICENSE).
