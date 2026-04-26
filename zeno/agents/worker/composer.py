from __future__ import annotations

from zeno.agents.models import AgentContext


def build_system_prompt(
    *, agent_type: str, agent_responsibilities: str | None, chroma_context: AgentContext, working_directory: str
) -> str:
    parts: list[str] = []

    # 1) Role definition
    parts.append(
        "\n".join(
            [
                (
                    f"You are a {agent_type} specialist agent working as part of Zeno, "
                    "a multi-agent orchestration system."
                ),
                (
                    "You will be given a specific task to complete. "
                    "Other specialist agents handle the rest of the system — "
                    "focus entirely on your assigned task."
                ),
                f"Your working directory is: {working_directory}",
                "All files must be created within this directory.",
                "Always use relative paths or this absolute path as your base.",
                "Never write to paths outside this directory.",
            ]
        ).strip()
        + "\n"
    )

    # 2) Responsibilities
    resp = (agent_responsibilities or "").strip() or "(none provided)"
    parts.append("## Your responsibilities for this task")
    parts.append(resp)

    # 3) Project context (from ChromaDB)
    parts.append("## Project context")
    summary = (chroma_context.session_summary or "").strip()
    parts.append(summary if summary else "(no session summary)")

    relevant = [t.strip() for t in (chroma_context.relevant_traces or []) if t.strip()]
    if relevant:
        parts.append("\n### Relevant prior work")
        for i, txt in enumerate(relevant, start=1):
            parts.append(f"**Trace {i}:**\n{txt}")

    history = [t.strip() for t in (chroma_context.agent_logs or []) if t.strip()]
    if history:
        parts.append("\n### Your prior history on this project")
        for i, txt in enumerate(history, start=1):
            parts.append(f"**Entry {i}:**\n{txt}")

    # 4) Behavioral rules
    parts.append("## Rules")
    at = (agent_type or "").strip().lower()
    if at in {"testing", "validation"}:
        parts.append(
            "- Complete your task fully before reporting back\n"
            "- Work only within your assigned scope — do not touch unrelated files\n"
            "- Install dependencies as needed to run and validate the code\n"
            "- Execute the code/tests and verify correctness\n"
            "- Report test results and any failures clearly\n"
            "- If you encounter a blocker, document it clearly in your log"
        )
    elif at in {"integration", "merge"}:
        parts.append(
            "- Complete your task fully before reporting back\n"
            "- Work only within your assigned scope — do not touch unrelated files\n"
            "- You may use git commands to merge branches and resolve conflicts\n"
            "- Do NOT add new features unrelated to resolving integration issues\n"
            "- If you encounter a blocker, document it clearly in your log"
        )
    else:
        parts.append(
            "- Complete your task fully before reporting back\n"
            "- Work only within your assigned scope — do not touch unrelated files\n"
            "- Do NOT install dependencies or packages\n"
            "- Do NOT run or execute code\n"
            "- Only write, create, and modify files\n"
            "- Leave execution and validation to the testing agent\n"
            "- If you encounter a blocker, document it clearly in your log"
        )

    # 5) Field Rules
    parts.append("## Field Rules")
    parts.append(
        "`type` must be exactly 'success' or 'terminate'\n"
        "`summary` must be a clear description of what you did and why\n"
        "`artifacts` must be a list of files you created, updated, or deleted\n"
        "`log` must be a detailed log entry for the next agent\n"
    )

    # Terminate Rules
    parts.append("## Terminate Rules")
    parts.append(
        "Only use `terminate` when the request cannot reasonably be planned"
        "or the task is impossible to complete. Use `success` otherwise.\n"
        "Always provide a clear, specific `reason` explaining why the request cannot proceed\n"
    )

    return "\n\n".join(parts).strip() + "\n"