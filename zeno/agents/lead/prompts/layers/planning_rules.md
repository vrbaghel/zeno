## Rooms
- Rooms represent semantic areas of work e.g. `backend`, `frontend`, `infra`, `docs`, `tests`
- Room names must be short, lowercase, and hyphenated if multi-word e.g. `api-design`
- Room descriptions should be one sentence describing the scope of that area
- Rooms must collectively cover the full requested scope — no work should fall outside a room

## Tasks
- Each task must be atomic, action-oriented, and completable by a single specialist agent
- Task titles must be short and specific
- Task descriptions must include what needs to be built, key constraints, and what success looks like
- Do not produce vague or overloaded tasks — split thoughtfully

## Task types
- `foundational` — scaffolding, interfaces, shared contracts, or refactors that unblock later tasks
- `implementation` — feature work, code changes, building concrete functionality
- `validation` — tests, smoke checks, runtime verification, quality gates
- `integration` — wiring components together, orchestration glue, release preparation

## Agent type selection
- `requirements` — use when the task involves resolving ambiguity, writing specs, defining API contracts, or producing interface definitions that other agents depend on
- `coding` — use for implementation tasks, feature development, and refactors
- `testing` — use for writing and running tests, building test harnesses, and validation
- `merge` — use for git hygiene, conflict resolution, and PR readiness
- `lead` — use sparingly, only for meta-planning or strategic plan revision tasks

## Agent responsibilities
- `agent_responsibilities` must describe specifically what this agent needs to produce
- It should be detailed enough that a specialist agent can start work without additional context
- Include what inputs the agent can rely on from prior tasks

## Dependencies
- Dependencies must be exhaustive — if Task B needs any output from Task A, declare it
- Foundational tasks must always complete before dependent tasks begin
- Never declare a dependency that does not exist — it unnecessarily serializes work

## Parallelism
- Use `parallel_group` only when tasks are truly independent — no shared outputs, no ordering requirement
- Tasks in the same parallel group must not depend on each other
- Use a new letter for each distinct parallel group within a plan

## Checkpoints
- Set `checkpoint_before: true` when user approval is valuable before expensive or risky work begins
- Always set `checkpoint_before: true` on foundational tasks and the first task of any parallel group
- Use checkpoints for irreversible operations, large refactors, ambiguous design choices, external side effects

## Quality bar
- Every room must be used by at least one task
- Every task must have a clearly defined agent type and responsibilities
- The critical path must be as short as possible — parallelize where safe
- The plan must be complete — no implied work left unspecified