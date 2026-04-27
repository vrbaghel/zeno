from __future__ import annotations

from pathlib import Path

from zeno.agents.lead.renderer import render_stage_context
from zeno.core.enums import ExecutionMode, LeadAgentStage


PACKAGE_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts" / "layers"


def _load_layer(layer_name: str) -> str:
    override_path = Path.home() / ".zeno/prompts/lead" / layer_name
    if override_path.exists():
        return override_path.read_text(encoding="utf-8")
    return (PACKAGE_PROMPTS_DIR / layer_name).read_text(encoding="utf-8")


def _render_agent_context_block(context) -> str:
    ctx = getattr(context, "agent_context", None)
    parts: list[str] = []
    parts.append("=== AGENT CONTEXT ===")
    parts.append("")
    parts.append("Session summary:")
    if ctx is None:
        parts.append("(none)")
        return "\n".join(parts).strip() + "\n"

    parts.append(ctx.session_summary.strip() if getattr(ctx, "session_summary", "") else "(none)")

    relevant = "\n\n".join([b.strip() for b in (ctx.relevant_traces or []) if b.strip()]).strip()
    if relevant:
        parts.append("")
        parts.append("Relevant prior work:")
        parts.append(relevant)

    history = "\n\n".join([b.strip() for b in (ctx.agent_logs or []) if b.strip()]).strip()
    if history:
        parts.append("")
        parts.append("Agent history:")
        parts.append(history)

    return "\n".join(parts).strip() + "\n"


def compose_system_prompt(mode: ExecutionMode) -> str:
    """Static standing instructions only (sent on first SDK session / when forced)."""
    layer_files: list[str] = [
        "identity.md",
        "response_format.md",
        "planning_rules.md",
        "mode_hitl.md" if mode == ExecutionMode.HITL else "mode_yolo.md",
    ]
    parts = [_load_layer(lf).strip() for lf in layer_files]
    return "\n\n".join([p for p in parts if p.strip()]).strip() + "\n"


def compose_user_message(stage: LeadAgentStage, context) -> str:
    """Per-call user message: stage prose + rendered runtime context."""
    stage_file = {
        LeadAgentStage.INITIAL: "stage_initial.md",
        LeadAgentStage.REVISION: "stage_revision.md",
        LeadAgentStage.CONTINUATION: "stage_continuation.md",
    }[stage]

    parts: list[str] = [
        _load_layer(stage_file).strip(),
        _load_layer("response_format.md").strip(),
        render_stage_context(stage, context).strip(),
    ]
    if stage == LeadAgentStage.INITIAL:
        parts.append(_render_agent_context_block(context).strip())

    return "\n\n".join([p for p in parts if p.strip()]).strip() + "\n"


def compose_prompt(mode: ExecutionMode, stage: LeadAgentStage, context) -> str:
    """
    Backward-compatible shim: concatenates system + user (legacy callers only).
    Prefer compose_system_prompt + compose_user_message for SDK integration.
    """
    return (
        compose_system_prompt(mode).strip()
        + "\n\n"
        + compose_user_message(stage, context).strip()
        + "\n"
    )
