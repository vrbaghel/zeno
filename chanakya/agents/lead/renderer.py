from __future__ import annotations

from chanakya.agents.lead.adapter import LeadAgentContext
from chanakya.core.enums import LeadAgentStage


def render_stage_context(stage: LeadAgentStage, context: LeadAgentContext) -> str:
    if stage == LeadAgentStage.INITIAL:
        return _render_initial_context(context)
    if stage == LeadAgentStage.REVISION:
        return _render_revision_context(context)
    return f"=== CONTEXT ===\n\nUnsupported stage: {stage}\n"


def _render_initial_context(context: LeadAgentContext) -> str:
    existing = (
        "\n".join([f"- {r}" for r in (context.existing_rooms or [])]).strip()
        or 'None — this is a new project'
    )

    prior_blocks = "\n\n".join([b.strip() for b in context.agent_context.relevant_prior_work if b.strip()])
    if not prior_blocks.strip():
        prior_blocks = "(no prior work)"

    history_blocks = "\n\n".join([b.strip() for b in context.agent_context.agent_history if b.strip()])
    if not history_blocks.strip():
        history_blocks = "No prior lead agent history"

    parts: list[str] = []
    parts.append("=== CURRENT CONTEXT ===")
    parts.append("")
    parts.append(f"Session ID: {context.session_id}")
    parts.append(f"Working Directory: {context.working_directory}")
    parts.append(f"User Prompt: {context.raw_input}")
    parts.append("")
    parts.append("Existing Rooms: " + existing)
    parts.append("")
    parts.append("Prior Project Context:")
    parts.append(prior_blocks)
    parts.append("")
    parts.append("Agent History:")
    parts.append(history_blocks)
    return "\n".join(parts).strip() + "\n"


def _render_revision_context(context: LeadAgentContext) -> str:
    revision_reason = context.revision_reason or "(none provided)"
    completed = context.completed_tasks or []

    completed_block = "\n".join([f"- {cid}" for cid in completed]).strip() or "(none)"

    pending_titles: list[str] = []
    if context.current_plan is not None and getattr(context.current_plan, "tasks", None):
        try:
            for t in context.current_plan.tasks:
                if getattr(t, "id", None) in set(completed):
                    continue
                pending_titles.append(getattr(t, "title", "") or getattr(t, "id", ""))
        except Exception:
            pending_titles = []

    pending_block = "\n".join([f"- {t}" for t in pending_titles if t.strip()]).strip() or "(none)"

    plan_text = ""
    if context.current_plan is not None:
        try:
            plan_text = context.current_plan.model_dump_json(indent=2)
        except Exception:
            plan_text = str(context.current_plan)
    if not plan_text.strip():
        plan_text = "(no current plan)"

    prior_blocks = "\n\n".join([b.strip() for b in context.agent_context.relevant_prior_work if b.strip()])
    if not prior_blocks.strip():
        prior_blocks = "(no prior work)"

    parts: list[str] = []
    parts.append("=== REVISION CONTEXT ===")
    parts.append("")
    parts.append(f"Session ID: {context.session_id}")
    parts.append(f"User Prompt: {context.raw_input}")
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

