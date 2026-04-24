from __future__ import annotations

from uuid import UUID

from chanakya.memory.models import MemContext, MemWing
from chanakya.memory.store import get_agent_history, search_drawers


def _summarize_session(current_session_tasks) -> str:
    # `current_session_tasks` is expected to be a list of DbTask-like objects.
    completed = [t for t in current_session_tasks if getattr(t, "status", None) == "completed"]
    running = [t for t in current_session_tasks if getattr(t, "status", None) == "running"]
    pending = [t for t in current_session_tasks if getattr(t, "status", None) == "pending"]

    lines: list[str] = []
    lines.append("Current session summary:")
    if completed:
        lines.append("")
        lines.append("Completed:")
        for t in completed[-10:]:
            lines.append(f"- {getattr(t, 'title', '')}".strip())
    if running:
        lines.append("")
        lines.append("Running:")
        for t in running[-10:]:
            lines.append(f"- {getattr(t, 'title', '')}".strip())
    if pending:
        lines.append("")
        lines.append("Pending:")
        for t in pending[:10]:
            lines.append(f"- {getattr(t, 'title', '')}".strip())
    return "\n".join([l for l in lines if l]).strip() + "\n"


def build_context(
    working_directory: str,
    wing: MemWing,
    task_description: str,
    agent_type: str,
    session_id: UUID,
    current_session_tasks,
) -> MemContext:
    session_summary = _summarize_session(current_session_tasks)
    relevant = search_drawers(
        working_directory,
        query=task_description,
        wing=wing.name,
        room=None,
        limit=5,
    )
    history = get_agent_history(working_directory, wing=wing.name, agent_type=agent_type, limit=3)
    return MemContext(session_summary=session_summary, relevant_drawers=relevant, agent_history=history)

