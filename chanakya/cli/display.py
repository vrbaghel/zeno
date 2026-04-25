from __future__ import annotations

from datetime import datetime
from uuid import UUID

from rich.console import Console

from chanakya.agents.models import (
    AdaptorError,
    AdaptorMetrics,
    AdaptorResponse,
    CheckpointContent,
    CheckpointOption,
)
from chanakya.core.enums import ExecutionMode, OrchestratorState
from chanakya.core.mode import OperationMode
from chanakya.orchestrator.errors import ChanakyaError


def _fmt_list(items: list[str]) -> str:
    if not items:
        return "(none)"
    if len(items) == 1:
        return items[0]
    return ", ".join(items)


def print_adaptor_result(
    console: Console,
    *,
    response: AdaptorResponse | None = None,
    metrics: AdaptorMetrics | None = None,
    error: AdaptorError | None = None,
) -> None:
    if error is not None:
        console.print("── Error " + "─" * 35)
        console.print(f"  Code       : {error.code}")
        console.print(f"  Message    : {error.message}")
        console.print(f"  Recoverable: {'yes' if error.recoverable else 'no'}")
        if error.request_id:
            console.print(f"  Request ID : {error.request_id}")
        if error.agent_id:
            console.print(f"  Agent ID   : {error.agent_id}")
        return

    assert response is not None
    assert metrics is not None

    console.print("── AdaptorResponse " + "─" * 26)
    console.print(f"  Status      : {response.status.value}")
    console.print(f"  Agent ID    : {response.agent_id}")
    console.print(f"  Request ID  : {response.request_id}")
    console.print(f"  Session ID  : {response.session_id}")
    console.print(f"  Created At  : {response.created_at}")
    console.print()

    console.print("  Payload")
    for m in response.payload.messages:
        console.print(f"    [{m.role}]: {m.content}")
    if not response.payload.messages:
        console.print("    (none)")
    console.print()

    console.print("  Artifacts")
    console.print(f"    Created  : {_fmt_list(response.artifacts.created)}")
    console.print(f"    Updated  : {_fmt_list(response.artifacts.updated)}")
    console.print(f"    Deleted  : {_fmt_list(response.artifacts.deleted)}")
    console.print()

    console.print("── AdaptorMetrics " + "─" * 27)
    console.print(f"  Provider    : {metrics.provider}")
    console.print(f"  Mode        : {metrics.mode}")
    console.print(f"  Model       : {metrics.model or '(as reported)'}")
    console.print()

    console.print("  Timing")
    console.print(f"    Latency             : {metrics.timing.latency_ms} ms")
    console.print(f"    Time to first token : {metrics.timing.time_to_first_token_ms} ms")
    console.print()

    # With the unified tiktoken strategy, we treat this as an estimate with a per-provider deviation label.
    console.print("  Tokens")
    console.print(f"    Input      : {metrics.tokens.input}")
    console.print(f"    Output     : {metrics.tokens.output}")
    console.print(f"    Total      : {metrics.tokens.total}")
    console.print("    Estimated  : yes")
    console.print("    Method     : tiktoken_cl100k")
    console.print(f"    Deviation  : {metrics.tokens.deviation or '(unknown)'}")
    console.print()

    console.print("── Error " + "─" * 35)
    console.print("  (none)")


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _line(console: Console, icon: str, message: str, *, ok: bool = False) -> None:
    sym = "✓" if ok else "⟳"
    console.print(f"  [{_ts()}] {sym} {message}")


def print_welcome(
    console: Console,
    *,
    version: str,
    execution_mode: ExecutionMode,
    operation_mode: OperationMode,
    adapter_label: str,
    wing_name: str,
) -> None:
    console.print(f"[bold]Chanakya[/bold] v{version}")
    console.print(
        f"  Execution: [cyan]{execution_mode.value}[/cyan]  "
        f"Operation: [cyan]{operation_mode.value}[/cyan]  "
        f"Adaptor: [cyan]{adapter_label}[/cyan]  "
        f"Wing: [cyan]{wing_name}[/cyan]"
    )
    console.print("  Type a task, or [dim]/help[/dim] for commands.")
    console.print()


def print_state_transition(console: Console, state: OrchestratorState) -> None:
    mapping: dict[OrchestratorState, tuple[bool, str]] = {
        OrchestratorState.INITIALIZING: (False, "Initializing session"),
        OrchestratorState.AWAITING_LEAD: (False, "Consulting lead agent"),
        OrchestratorState.PLANNING: (False, "Planning"),
        OrchestratorState.EXECUTING: (False, "Executing"),
        OrchestratorState.MERGING: (False, "Merging changes"),
        OrchestratorState.COMPLETED: (True, "Session complete"),
        OrchestratorState.FAILED: (True, "Session failed"),
        OrchestratorState.ABORTED: (True, "Session aborted"),
        OrchestratorState.AWAITING_HUMAN: (False, "Awaiting human"),
    }
    ok, msg = mapping.get(state, (False, state.value))
    _line(console, "", msg, ok=ok)


def print_task_activity(
    console: Console,
    *,
    task_title: str,
    agent_type: str,
    status: str,
) -> None:
    _line(console, "", f"{task_title} ({agent_type}): {status}")


async def print_checkpoint(console: Console, content: CheckpointContent) -> str:
    from chanakya.cli.input import async_input

    console.print("  ──────────────────────────────────")
    console.print("  [bold]Chanakya needs your input:[/bold]")
    console.print()
    console.print(f"  [bold]{content.title}[/bold]")
    console.print(f"  {content.description}")
    console.print()

    opts = content.options or [
        CheckpointOption(key="approve", label="Approve"),
        CheckpointOption(key="revise", label="Revise"),
        CheckpointOption(key="cancel", label="Cancel"),
    ]
    labels = "   ".join(f"[{o.key[0].upper()}] {o.label}" for o in opts)
    console.print(f"  {labels}")

    key_by_letter = {o.key[0].upper(): o.key for o in opts}
    key_by_key = {o.key.upper(): o.key for o in opts}

    while True:
        raw = (await async_input("  > ")).strip().upper()
        if raw in key_by_letter:
            return key_by_letter[raw]
        if raw in key_by_key:
            return key_by_key[raw]
        console.print("  [dim]Enter one of: " + ", ".join(sorted(key_by_letter.keys())) + ".[/dim]")


def print_progress_note(console: Console, message: str, *, ok: bool = False) -> None:
    _line(console, "", message, ok=ok)


def print_completion_summary(
    console: Console,
    *,
    task_count: int,
    files_created: int,
    files_updated: int,
    files_deleted: int,
    tokens_total: int | None,
    elapsed_s: float,
) -> None:
    tok = tokens_total if tokens_total is not None else "?"
    console.print(
        f"  [bold]Summary:[/bold] {task_count} task(s) | "
        f"{files_created} created, {files_updated} updated, {files_deleted} deleted | "
        f"{tok} tokens | {elapsed_s:.1f}s"
    )
    console.print()


def print_error(console: Console, error: ChanakyaError) -> None:
    console.print(f"  [red]Error:[/red] {error.message}")


def print_help(console: Console) -> None:
    console.print("  [bold]Commands[/bold]")
    console.print("    /quit     Exit Chanakya")
    console.print("    /status   Session and orchestrator state")
    console.print("    /help     This help")
    console.print()


def print_status(
    console: Console,
    *,
    session_id: UUID | None,
    orchestrator_state: OrchestratorState | None,
    working_directory: str,
    execution_mode: ExecutionMode,
) -> None:
    console.print("  [bold]Status[/bold]")
    console.print(f"    Working directory: {working_directory}")
    console.print(f"    Execution mode:    {execution_mode.value}")
    if session_id is None:
        console.print("    Session:           (none active)")
    else:
        console.print(f"    Session ID:        {session_id}")
    if orchestrator_state is None:
        console.print("    Orchestrator state: (n/a)")
    else:
        console.print(f"    Orchestrator state: {orchestrator_state.value}")
    console.print("    [dim]TODO Phase 9: task list, metrics history, resume tokens.[/dim]")
    console.print()

