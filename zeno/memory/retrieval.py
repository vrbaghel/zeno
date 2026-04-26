from __future__ import annotations

from uuid import UUID

from zeno.memory.models import MemContext, MemVault
from zeno.memory.store import get_agent_logs, search_traces


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
    vault: MemVault,
    task_description: str,
    agent_type: str,
    agent_id: str,
    session_id: UUID,
    current_session_tasks,
) -> MemContext:
    session_summary = _summarize_session(current_session_tasks)
    relevant = search_traces(
        working_directory,
        query=task_description,
        vault=vault.name,
        room=None,
        limit=5,
    )
    history = get_agent_logs(working_directory, vault=vault.name, agent_id=agent_id, limit=3)
    return MemContext(session_summary=session_summary, relevant_traces=relevant, agent_logs=history)

