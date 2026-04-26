# Zeno Process Flow — "Create a Weather App"

## 1. User Invokes Zeno

```
User types: zeno
```

- CLI starts, reads config from `~/.zeno/config.toml`
- Detects working directory — `/home/user/weather-app`
- Checks `.zeno/` exists — creates silently if not
- Checks git initialized — initializes silently if not
- Checks memory store exists — initializes vault `weather-app` if not
- Checks SQLite DB exists — creates if not
- Prints welcome banner
- Opens input loop — `>` prompt appears

---

## 2. User Submits Prompt

```
> Create a weather app
```

- CLI parses input — not a slash command, treated as task submission
- CLI calls `orchestrator.run("Create a weather app")`
- Orchestrator creates a new `DbSession` in SQLite
  - `raw_input`: "Create a weather app"
  - `execution_mode`: HITL
  - `orchestrator_state`: INITIALIZING
- Orchestrator transitions state → AWAITING_LEAD

---

## 3. Lead Agent Dispatched

Orchestrator prepares lead agent context:

- Fetches existing rooms from SQLite → none, new project
- Fetches ChromaDB context
  - Semantic search: "Create a weather app" → no prior drawers yet
  - Agent history: no prior lead agent entries
- Composes lead agent prompt
  - identity layer
  - response format layer
  - planning rules layer
  - mode_hitl layer
  - stage_initial layer
  - dynamic context — working directory, empty rooms, empty ChromaDB findings
- Orchestrator opens `ClaudeSDKClient` session
- Sends composed prompt as initial message
- SDK manages the Claude process internally — no subprocess management by Zeno

---

## 4. Lead Agent Clarification (HITL Mode)

Lead agent reasons about the task — "Create a weather app" is ambiguous. It uses `AskUserQuestion` tool natively through the SDK:

```
Zeno: Before I start planning, I need some clarity:

  What type of weather app do you want to build?
  [A] Web app (React/Vue frontend)
  [B] CLI tool (terminal based)
  [C] Mobile app (React Native)
```

- SDK surfaces `AskUserQuestion` to Zeno via the message stream
- Orchestrator detects it — transitions state → AWAITING_HUMAN
- CLI displays the question inline at the `>` prompt
- User responds: A

```
  Which weather data provider should I use?
  [A] OpenWeatherMap API
  [B] WeatherAPI
  [C] Let the agent decide
```

- User responds: A

```
  What features should be included?
  [A] Current weather + 5 day forecast + location search
  [B] Current weather only
  [C] Full featured — alerts, radar, hourly, daily
```

- User responds: A

- Answers flow back to `ClaudeSDKClient` via SDK's native answer handling
- Lead agent receives answers, continues reasoning in same session
- Orchestrator transitions state → AWAITING_LEAD

---

## 5. Lead Agent Produces Execution Plan

Lead agent now has enough clarity. It produces a structured `ExecutionPlanResponse` as its final output using `output_format` JSON schema:

```
task_summary: "Build a React web app with OpenWeatherMap integration,
               current weather, 5 day forecast, location search"

rooms:
  - requirements  "project spec and API contracts"
  - backend       "API integration and data models"
  - frontend      "React components and UI"
  - testing       "validation and test coverage"

tasks:
  task-1: Requirements & Specification
    agent_type: requirements
    agent_responsibilities: "Produce complete spec covering API contracts,
                             component structure, data models, feature definitions"
    type: foundational
    room: requirements
    depends_on: []
    parallel_group: null
    checkpoint_before: true

  task-2: Backend API Integration
    agent_type: backend
    agent_responsibilities: "Implement OpenWeatherMap integration,
                             WeatherData model, error handling, .env setup"
    type: implementation
    room: backend
    depends_on: [task-1]
    parallel_group: A
    checkpoint_before: false

  task-3: Frontend Development
    agent_type: frontend
    agent_responsibilities: "Build React components — CurrentWeather,
                             Forecast, LocationSearch — consuming WeatherData model"
    type: implementation
    room: frontend
    depends_on: [task-1]
    parallel_group: A
    checkpoint_before: false

  task-4: Integration & Merge
    agent_type: integration
    agent_responsibilities: "Merge frontend and backend branches,
                             verify API contract consistency, resolve conflicts"
    type: integration
    room: backend + frontend
    depends_on: [task-2, task-3]
    parallel_group: null
    checkpoint_before: false

  task-5: Testing & Validation
    agent_type: testing
    agent_responsibilities: "Write and run tests, validate all features,
                             address open issues from prior agents"
    type: validation
    room: testing
    depends_on: [task-4]
    parallel_group: null
    checkpoint_before: false

diary_entry:
  summary: "Decomposed weather app into 5 tasks. React frontend,
            OpenWeatherMap backend, parallel implementation phase."
  decisions: "Separated backend and frontend into parallel group A
              since they can work independently after requirements."
  assumptions: "User wants production-ready code, mobile responsive."
  dependencies: "All implementation tasks depend on requirements spec."
  open_issues: "API key management strategy to be decided by backend agent."
  room: requirements
```

- `ClaudeSDKClient` session ends — lead agent's job is done for now
- SDK session_id saved to `DbSession` for potential future resumption
- Orchestrator transitions state → PLANNING

---

## 6. Orchestrator Parses and Saves Plan

`orchestrator/planner.py` processes `ExecutionPlanResponse`:

- Validates response against contract rules
- Creates `DbExecutionPlan` in SQLite — status: draft
- Creates `DbRoom` records in SQLite — requirements, backend, frontend, testing
- Maps local task IDs to UUIDs — `task-1` → `uuid-abc` etc.
- Creates `DbTask` records with all fields — dependencies use UUID mapping
- Creates `DbAgentAssignment` records — one per task, status: assigned
- Saves lead agent diary entry to ChromaDB
  - vault: weather-app
  - room: requirements
  - agent_type: lead
  - document: full diary entry text verbatim
- Updates `DbExecutionPlan` status → active

---

## 7. HITL Plan Approval Checkpoint

Since execution mode is HITL and task-1 has `checkpoint_before: true`:

- Orchestrator creates `DbCheckpoint` — type: PLAN_APPROVAL
- Transitions state → AWAITING_HUMAN
- CLI displays plan summary:

```
  Zeno has a plan:

  1. Requirements & Specification  [foundational]
  2. Backend API Integration       ┐ parallel group A
  3. Frontend Development          ┘
  4. Integration & Merge           [sequential]
  5. Testing & Validation          [sequential]

  Assumptions:
  - Production-ready code expected
  - Mobile responsive required

  [A] Approve   [B] Reject   [C] Modify
  >
```

- User responds: A
- Orchestrator resolves checkpoint — status: approved
- Transitions state → EXECUTING

---

## 8. Task 1 — Requirements Agent

**Pre-dispatch:**
- Orchestrator creates worktree
  - branch: `zeno/<session_id>/task-1`
  - path: `.zeno/worktrees/<session_id>/task-1`
- Updates `DbTask` with worktree_path and branch_name
- Fetches ChromaDB context for requirements agent
  - semantic search: "requirements specification weather app API contracts"
  - no prior drawers yet — empty context
  - no agent history yet
- Dynamically builds worker system prompt
  - role: "You are a requirements agent..."
  - responsibilities: from `task.agent_responsibilities`
  - ChromaDB context: empty for first task
  - diary entry instructions: how to write a detailed diary entry
  - response format: include diary_entry in final response

**Dispatch via SDK:**
- `query()` called with dynamically constructed `ClaudeAgentOptions`
  - `system_prompt`: dynamically built worker prompt
  - `allowed_tools`: Read, Write, Edit, Bash, Glob, Grep
  - `permission_mode`: acceptEdits
  - `cwd`: worktree path
  - `hooks`: PostToolUse artifact tracker, Stop diary collector
- SDK manages agent execution entirely
- Agent reads existing project files if any, creates `docs/spec.md`

**PostToolUse hook fires** on every file operation:
- Write `docs/spec.md` → Zeno records artifact
  - operation: created
  - path: docs/spec.md
  - saved to `DbArtifact`

**Agent completes:**
- Stop hook fires — Zeno extracts diary entry from agent's final response
- `ResultMessage` received — exact metrics extracted
  - input_tokens, output_tokens, total_cost_usd, duration_ms
- Metrics saved to `DbTaskMetrics`
- Artifacts saved to `DbArtifact` records
- Diary entry saved to ChromaDB
  - vault: weather-app
  - room: requirements
  - agent_type: requirements
  - document: full diary entry verbatim
- `DbAgentAssignment` marked complete
- `DbTask` marked complete

**Merge:**
- Orchestrator calls `git.merge_worktree()` — merges task-1 branch into main
- Worktree cleaned up — path and branch deleted
- `DbTask` worktree_path and branch_name cleared
- CLI prints: `✓ Task complete: Requirements & Specification`

---

## 9. Tasks 2 & 3 — Parallel Group A

Both tasks depend only on task-1 which is complete. Orchestrator identifies parallel group A.

**Pre-dispatch checkpoint:**
- `task-2` has `checkpoint_before: false` — no pause needed
- Parallel group fires immediately

**Worktrees created for both:**
```
branch: zeno/<session_id>/task-2  →  backend
branch: zeno/<session_id>/task-3  →  frontend
```

**ChromaDB context fetched for each:**

Task-2 backend agent query:
- semantic search: "backend API integration OpenWeatherMap WeatherData"
- returns: requirements agent drawer — API contracts, WeatherData model, component structure
- agent history: no prior backend agent entries

Task-3 frontend agent query:
- semantic search: "frontend React components weather app UI"
- returns: requirements agent drawer — component structure, feature list, WeatherData model
- agent history: no prior frontend agent entries

Both agents receive their own dynamically built system prompts with ChromaDB context injected. They know exactly what the requirements spec decided without reading the file — because the requirements agent's diary entry contains the critical decisions verbatim.

**Both dispatched via separate `query()` calls:**
- Backend agent works in its worktree — creates `src/api/weather.js`, `src/models/WeatherData.js`, `.env.example`
- Frontend agent works in its worktree — creates `src/components/CurrentWeather.jsx`, `src/components/Forecast.jsx`, `src/components/LocationSearch.jsx`, `src/App.jsx`

Because only one Gemini provider is available in adapter mode — actually, with the SDK this constraint disappears. The SDK manages one Claude process per `query()` call. Both can run concurrently as separate asyncio tasks.

**Both complete:**
- PostToolUse hooks capture all file operations
- Stop hooks collect diary entries
- ResultMessages yield exact metrics for each
- Both diary entries saved to ChromaDB
  - backend agent → room: backend
  - frontend agent → room: frontend
- Both tasks marked complete in SQLite

**Sequential merge:**
- task-2 branch merged into main first
- task-3 branch merged — potential conflicts resolved by merge agent
- Both worktrees cleaned up
- CLI prints completion for both

---

## 10. Task 4 — Integration Agent

**ChromaDB context fetched:**
- semantic search: "integration merge frontend backend weather app"
- returns top results:
  - requirements drawer — full spec, API contracts
  - backend drawer — WeatherData model location, axios setup, retry logic
  - frontend drawer — component structure, import paths used
- agent history: no prior integration agent entries

Integration agent receives all three prior agents' decisions in its prompt. It knows:
- Exactly where the WeatherData model lives
- What import path the frontend used
- What API endpoints the backend implemented
- What open issues were flagged

**Dispatch:**
- Agent verifies API contract consistency between frontend and backend
- Resolves any conflicts
- Produces integrated codebase

**Completion:**
- Diary entry saved to ChromaDB
- Task marked complete

---

## 11. Task 5 — Testing Agent

**ChromaDB context fetched:**
- semantic search: "testing validation weather app"
- returns all four prior drawers — full project history
- open issues surfaced from prior diary entries:
  - "API key management" flagged by backend agent
  - "Error handling for API failures" flagged by requirements agent
  - "Rate limiting not implemented" flagged by backend agent

Testing agent has full visibility into every decision, every assumption, every open issue — without having read a single file. All from ChromaDB.

**Dispatch:**
- Agent writes tests, runs them, validates all features
- Addresses flagged open issues

**Completion:**
- Final diary entry saved to ChromaDB
- Task marked complete
- Session marked COMPLETED in SQLite

---

## 12. Session Complete

```
  ✓ Session complete

  Summary:
    5 tasks completed
    4 rooms created
    23 files created/modified
    Total tokens: 48,234
    Total cost: $0.87
    Duration: 4m 32s

>
```

- Input loop returns
- User can submit next prompt or type `/quit`

---

## Key Flows Summarized

```
User prompt
  → Orchestrator creates session
  → Lead agent (ClaudeSDKClient) clarifies + plans
  → Orchestrator saves plan to SQLite
  → HITL checkpoint (if HITL mode)
  → For each task in dependency order:
      → Fetch ChromaDB context
      → Build dynamic worker prompt
      → Create worktree
      → SDK query() dispatches agent
      → PostToolUse hooks track artifacts
      → Stop hook collects diary entry
      → ResultMessage yields exact metrics
      → Save diary to ChromaDB
      → Save metrics + artifacts to SQLite
      → Merge worktree
  → Session complete
```

Does this flow feel complete and correct to you?