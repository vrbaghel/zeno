from __future__ import annotations

from pathlib import Path

from chanakya.agents.lead.adapter import LeadAgentContext
from chanakya.agents.lead.renderer import render_stage_context
from chanakya.core.enums import ExecutionMode, LeadAgentStage

PACKAGE_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts" / "layers"


def _load_layer(layer_name: str) -> str:
    override_path = Path.home() / ".chanakya/prompts/lead" / layer_name
    if override_path.exists():
        return override_path.read_text(encoding="utf-8")
    return (PACKAGE_PROMPTS_DIR / layer_name).read_text(encoding="utf-8")


def _render_agent_context_block(context: LeadAgentContext) -> str:
    ctx = context.agent_context
    parts: list[str] = []
    parts.append("=== AGENT CONTEXT ===")
    parts.append("")
    parts.append("Session summary:")
    parts.append(ctx.session_summary.strip() if ctx.session_summary else "(none)")

    relevant = "\n\n".join([b.strip() for b in ctx.relevant_prior_work if b.strip()]).strip()
    if relevant:
        parts.append("")
        parts.append("Relevant prior work:")
        parts.append(relevant)

    history = "\n\n".join([b.strip() for b in ctx.agent_history if b.strip()]).strip()
    if history:
        parts.append("")
        parts.append("Agent history:")
        parts.append(history)

    return "\n".join(parts).strip() + "\n"


def compose_prompt(mode: ExecutionMode, stage: LeadAgentStage, context: LeadAgentContext) -> str:
    layer_files: list[str] = [
        "identity.md",
        "response_format.md",
        "planning_rules.md",
        "mode_hitl.md" if mode == ExecutionMode.HITL else "mode_yolo.md",
        "stage_initial.md" if stage == LeadAgentStage.INITIAL else "stage_revision.md",
    ]

    parts: list[str] = []
    for lf in layer_files:
        parts.append(_load_layer(lf).strip())

    parts.append(render_stage_context(stage, context).strip())
    parts.append(_render_agent_context_block(context).strip())

    return "\n\n".join([p for p in parts if p.strip()]).strip() + "\n"

