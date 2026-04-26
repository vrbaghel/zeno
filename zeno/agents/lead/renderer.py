from __future__ import annotations

from zeno.core.enums import LeadAgentStage


def render_stage_context(stage: LeadAgentStage, context) -> str:
    if stage == LeadAgentStage.INITIAL:
        return _render_initial_context(context)
    if stage == LeadAgentStage.REVISION:
        return _render_revision_context(context)
    return f"=== CONTEXT ===\n\nUnsupported stage: {stage}\n"


def _render_initial_context(context) -> str:
    existing = (
        "\n".join([f"- {r}" for r in (getattr(context, "existing_rooms", None) or [])]).strip()
        or "None — this is a new project"
    )

    agent_ctx = getattr(context, "agent_context", None)
    prior_blocks = ""
    if agent_ctx is not None:
        prior_blocks = "\n\n".join(
            [b.strip() for b in (getattr(agent_ctx, "relevant_traces", None) or []) if b.strip()]
        )
    if not prior_blocks.strip():
        prior_blocks = "(no prior work)"

    history_blocks = ""
    if agent_ctx is not None:
        history_blocks = "\n\n".join(
            [b.strip() for b in (getattr(agent_ctx, "agent_logs", None) or []) if b.strip()]
        )
    if not history_blocks.strip():
        history_blocks = "No prior lead agent history"

    parts: list[str] = []
    parts.append("=== CURRENT CONTEXT ===")
    parts.append("")
    parts.append(f"Session ID: {getattr(context, 'session_id', '')}")
    parts.append(f"Working Directory: {getattr(context, 'working_directory', '')}")
    parts.append(f"User Prompt: {getattr(context, 'raw_input', '')}")
    parts.append("")
    parts.append("Existing Rooms: " + existing)
    parts.append("")
    parts.append("Prior Project Context:")
    parts.append(prior_blocks)
    parts.append("")
    parts.append("Agent History:")
    parts.append(history_blocks)
    return "\n".join(parts).strip() + "\n"


def _render_revision_context(context) -> str:
    revision_reason = getattr(context, "revision_reason", None) or "(none provided)"
    completed = getattr(context, "completed_tasks", None) or []

    completed_block = "\n".join([f"- {cid}" for cid in completed]).strip() or "(none)"

    pending_titles: list[str] = []
    current_plan = getattr(context, "current_plan", None)
    if current_plan is not None and getattr(current_plan, "tasks", None):
        try:
            completed_set = set(completed)
            for t in current_plan.tasks:
                if getattr(t, "id", None) in completed_set:
                    continue
                pending_titles.append(getattr(t, "title", "") or getattr(t, "id", ""))
        except Exception:
            pending_titles = []

    pending_block = "\n".join([f"- {t}" for t in pending_titles if t.strip()]).strip() or "(none)"

    plan_text = ""
    if current_plan is not None:
        try:
            plan_text = current_plan.model_dump_json(indent=2)
        except Exception:
            plan_text = str(current_plan)
    if not plan_text.strip():
        plan_text = "(no current plan)"

    agent_ctx = getattr(context, "agent_context", None)
    prior_blocks = ""
    if agent_ctx is not None:
        prior_blocks = "\n\n".join(
            [b.strip() for b in (getattr(agent_ctx, "relevant_traces", None) or []) if b.strip()]
        )
    if not prior_blocks.strip():
        prior_blocks = "(no prior work)"

    parts: list[str] = []
    parts.append("=== REVISION CONTEXT ===")
    parts.append("")
    parts.append(f"Session ID: {getattr(context, 'session_id', '')}")
    parts.append(f"User Prompt: {getattr(context, 'raw_input', '')}")
    parts.append(f"Revision Reason: {revision_reason}")
    parts.append("")
    parts.append("Current Execution Plan:")
    parts.append(plan_text.strip())
    parts.append("")
    parts.append("Completed Tasks (DO NOT MODIFY):")
    parts.append(completed_block)
    parts.append("")
    parts.append("Pending Tasks (subject to revision):")
    parts.append(pending_block)
    parts.append("")
    parts.append("Prior Project Context:")
    parts.append(prior_blocks)
    return "\n".join(parts).strip() + "\n"

