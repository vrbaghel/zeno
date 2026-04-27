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
- Pick the label that most precisely describes the work — do not force non-coding tasks into coding vocabulary

## Agent type selection
- Choose an agent type that precisely describes the specialist's domain
- Use short, lowercase, hyphenated names — they become the agent's identity in its system prompt
- Reserve `testing` or `validation` agents for tasks that must execute code and verify correctness
- Reserve `integration` agent for tasks involving integrations
- All other types receive file-write-only permissions by default

## Agent responsibilities
A worker agent executes a unit of work which is one task.
- `agent_responsibilities` is the primary instruction the worker agent reads — write it as the reasoning brain telling the executor exactly what to do
- Structure it as a **numbered step-by-step plan** — not prose, not code, but ordered actions the agent should take
- Each step must be:
  - **Action-oriented**: start with a verb (Read, Identify, Create, Write, Update, Verify, etc)
  - **Non-code**: describe *what* to produce and *why*, not *how* to write it in code
- Include a final step that states what the completed output looks like (the success condition)
- Mention what inputs are available from prior tasks
- Do **not** write vague steps like "implement the feature" — break it down until each step is unambiguous
- Aim for 4-8 steps; fewer means the task is probably under-specified, more means the task should be split

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
- The plan must be complete — no implied work left unspecified across chunks (each chunk must be internally coherent)

## Chunked planning
- Produce **one logical phase per response** — do not dump the entire remaining plan at once
- Chunk boundaries are your decision — base them on logical phases, dependency boundaries, and task types
- Every **non-final** chunk must end with at least one task that has `checkpoint_before: true` — this is how Zeno knows to synchronize before the next chunk
- Set `is_final: true` only when no more tasks remain after this chunk
- Prioritize parallelism — use `parallel_group` to run up to **five** tasks concurrently within a chunk
- Never put more than five tasks in the same parallel group

## Continuation behavior
- When you receive an **EXECUTION UPDATE** (continuation stage), you are being asked for the **next chunk**
- Use **completed**, **running**, **pending**, and **failed** task lines in the update to sequence the next phase safely
- You already know everything you planned previously from session history — do not contradict it unless the update shows failure or a clear mismatch
- Do not re-plan tasks that are **completed** or **running** — only add or adjust work that logically follows
- If a task **failed**, your next chunk should address recovery or an alternate path before proceeding