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
- Use a short, lowercase, hyphenated label that describes the nature of the work
- Common examples (not exhaustive):
  - `foundational` — scaffolding, shared contracts, interfaces that unblock later tasks
  - `research` — competitor analysis, literature review, requirements discovery
  - `analysis` — data analysis, metrics review, audit, evaluation of options
  - `design` — UI/UX mockups, architecture diagrams, system design documents
  - `implementation` — building features, writing code, making concrete changes
  - `documentation` — writing specs, guides, READMEs, API references
  - `validation` — tests, smoke checks, runtime verification, quality gates
  - `integration` — wiring components together, orchestration glue, release prep
- Pick the label that most precisely describes the work — do not force non-coding tasks into coding vocabulary

## Agent type selection
- Choose an agent type that precisely describes the specialist's domain
- Use short, lowercase, hyphenated names — they become the agent's identity in its system prompt
- Common examples (not exhaustive): `coding`, `testing`, `research`, `data-analysis`,
  `legal-review`, `documentation`, `design`, `security-audit`, `devops`
- Reserve `testing` or `validation` for tasks that must execute code and verify correctness
- Reserve `integration` for tasks involving integrations
- All other types receive file-write-only permissions by default

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