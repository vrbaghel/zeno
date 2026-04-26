You are in the **initial** stage — produce the **first chunk** of the execution plan only.

## Goals
- Understand scope, complexity, and intent of the user request (see the user prompt and context below)
- Identify specialist agent types and define **rooms** for this chunk
- Output only tasks that belong in the **first logical phase** — not the full end-to-end plan

## How to approach this
- Start with foundational or unblocking work that can begin immediately
- If the entire user request is a single small phase, you may complete it in one chunk and set `is_final: true`
- Otherwise set `is_final: false` and ensure at least one task in this chunk has `checkpoint_before: true` before the chunk boundary
- In HITL mode — resolve critical ambiguities before planning (per mode rules)
- In YOLO mode — make explicit assumptions and proceed
- Think about what can run in parallel vs sequential within this chunk only (up to five tasks per parallel group)
