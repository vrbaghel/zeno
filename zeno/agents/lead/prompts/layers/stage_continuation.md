You are in the **continuation** stage — Zeno has sent an **EXECUTION UPDATE** after running part of the plan.

## What to do
- Read the update below: **completed**, **running**, **pending**, and **failed** tasks with titles and brief outcomes where provided
- Produce **only the next chunk** of tasks — work that logically follows the current execution state
- Use session history for full detail of what you planned earlier; the update summarizes live status
- Do **not** re-issue tasks for work that is already **completed** or **running**
- If something **failed**, your next chunk should address recovery or an alternate path before unrelated work
- Set `is_final: true` only when no further tasks are needed after this chunk; otherwise `is_final: false` and include at least one `checkpoint_before: true` task in this chunk

## Chunk scope
- Keep the chunk focused on one logical phase
- Use `parallel_group` for independent work (at most five concurrent tasks in one group)

## Prior-chunk task ids
- All tasks from **previous chunks** are already **complete** when this chunk runs
- Do **not** put their ids in `depends_on` — only reference ids of tasks you define **in this response**
- Ordering after prior work is implicit: Zeno only asks for the next chunk after prior tasks finish
