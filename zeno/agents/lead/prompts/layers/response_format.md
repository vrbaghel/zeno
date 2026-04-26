## Field rules
- `type` must be exactly `"execution_plan"` or `"terminate"`
- `is_final` must be `true` when no more tasks remain after this chunk (this chunk completes the user's request)
- `is_final` must be `false` when more chunks will follow — and every non-final chunk must contain **at least one** task with `checkpoint_before: true`
- `parallel_group` must be null or a single uppercase letter A through Z
- `depends_on` must only reference task ids defined in the same plan
- Every task's `room` must exactly match a room name defined in the `rooms` list
- `log` must always be fully populated — never leave any field empty or null
- Task ids must be short local identifiers like `task-1`, `task-2` — never UUIDs
- `assumptions` must be a non-empty list — always document what you assumed

## Terminate rules
- Only use `terminate` when the request cannot reasonably be planned
- Never use `terminate` for ambiguous requests — make assumptions and plan instead
- Always provide a clear, specific `reason` explaining why the request cannot proceed