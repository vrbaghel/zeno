from __future__ import annotations

from zeno.agents.models import AgentContext


def build_system_prompt(
    *, agent_type: str, agent_responsibilities: str | None, chroma_context: AgentContext
) -> str:
    # 1) Role definition
    parts: list[str] = []
    parts.append(
        f"You are a {agent_type} agent working as part of a multi-agent\n"
        "system called Zeno."
    )

    # 2) Responsibilities
    resp = (agent_responsibilities or "").strip() or "(none provided)"
    parts.append("Your specific responsibilities for this task:")
    parts.append(resp)

    # 3) Project context (from ChromaDB)
    parts.append("=== PROJECT CONTEXT ===")
    parts.append((chroma_context.session_summary or "").strip() or "(no session summary)")
    parts.append("")

    parts.append("RELEVANT PRIOR WORK:")
    relevant = [t.strip() for t in (chroma_context.relevant_prior_work or []) if t.strip()]
    if relevant:
        for i, txt in enumerate(relevant, start=1):
            parts.append(f"- Trace {i}:\n{txt}")
    else:
        parts.append("(none)")
    parts.append("")

    parts.append("AGENT HISTORY:")
    history = [t.strip() for t in (chroma_context.agent_history or []) if t.strip()]
    if history:
        for i, txt in enumerate(history, start=1):
            parts.append(f"- Entry {i}:\n{txt}")
    else:
        parts.append("No prior history for this agent type in this project")

    # 4) Behavioral rules
    parts.append("")
    parts.append("Rules you must follow:")
    parts.append("- Complete your task fully before responding")
    parts.append(
        "- Be exhaustive when reporting artifacts — include every file\n"
        "  you created, updated, or deleted including files modified\n"
        "  via bash commands"
    )
    parts.append(
        "- Write your log entry as a detailed briefing for the next\n"
        "  agent that will work on this project"
    )
    parts.append("- Assume no prior context beyond what is provided above")

    # 5) Response format rules
    parts.append("")
    parts.append("When your task is complete, use the StructuredOutput tool")
    parts.append("with this exact structure:")
    parts.append("- summary: what you did and why")
    parts.append("- artifacts.created: list of absolute file paths created")
    parts.append("- artifacts.updated: list of absolute file paths modified")
    parts.append("- artifacts.deleted: list of absolute file paths deleted")
    parts.append("- log.summary: detailed summary for future agents")
    parts.append("- log.decisions: key decisions made and reasoning")
    parts.append("- log.assumptions: anything assumed not explicitly specified")
    parts.append("- log.open_issues: unresolved things future agents should know")

    return "\n".join(parts).strip() + "\n"

