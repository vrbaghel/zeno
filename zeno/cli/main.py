from __future__ import annotations

import asyncio
import os
import signal
from uuid import UUID

import typer
from rich.console import Console

from zeno import __version__
from zeno.agents.models import CheckpointContent
from zeno.cli import display as cli_display
from zeno.cli.input import SlashCommand, TaskInput, async_input, parse_input
from zeno.core.enums import ExecutionMode, OrchestratorState
from zeno.core.logging import setup_logging
from zeno.db.models import TaskStatus
from zeno.orchestrator.core import OrchestratorCore
from zeno.orchestrator.errors import ZenoError

app = typer.Typer(add_completion=False, no_args_is_help=False)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    yolo: bool = typer.Option(False, "--yolo", help="YOLO execution mode (default is HITL)."),
    debug: bool = typer.Option(False, "--debug", help="Enable verbose debug logging to console."),
    version: bool = typer.Option(
        False,
        "--version",
        help="Show version and exit",
        is_eager=True,
    ),
) -> None:
    if version:
        typer.echo(f"Zeno v{__version__}")
        raise typer.Exit()

    if ctx.invoked_subcommand is not None:
        return

    console = Console()
    console.print(f"[bold]Zeno[/bold] v{__version__}")

    execution_mode = ExecutionMode.YOLO if yolo else ExecutionMode.HITL

    # Configure file/console logging early.
    setup_logging(os.getcwd(), debug=debug)
    # Ensure Ctrl+C / termination signals exit immediately.
    def _terminate(_signum, _frame) -> None:  # type: ignore[no-untyped-def]
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGINT, _terminate)
        signal.signal(signal.SIGTERM, _terminate)
    except Exception:
        # Signal handling is best-effort (platform / interpreter dependent).
        pass

    try:
        asyncio.run(
            _interactive_main(
                console=console,
                execution_mode=execution_mode,
            )
        )
    except KeyboardInterrupt:
        console.print("Goodbye.")
        raise typer.Exit(code=130)


async def _interactive_main(
    *,
    console: Console,
    execution_mode: ExecutionMode,
) -> None:
    cwd = os.getcwd()

    async def hitl_callback(content: CheckpointContent) -> str:
        return await cli_display.print_checkpoint(console, content)

    orchestrator = OrchestratorCore(
        execution_mode=execution_mode,
        working_directory=cwd,
        hitl_callback=hitl_callback if execution_mode == ExecutionMode.HITL else None,
    )

    try:
        await orchestrator.initialize_runtime()
    except ZenoError as e:
        cli_display.print_error(console, e)
        raise typer.Exit(code=1) from e

    cli_display.print_welcome(
        console,
        version=__version__,
        execution_mode=execution_mode,
        vault_name=orchestrator.vault_name,
    )

    try:
        try:
            resumable = await orchestrator.db_repo.get_resumable_sessions(cwd)
            if resumable:
                sess = resumable[0]
                plan = await orchestrator.db_repo.get_active_plan(sess.id)
                tasks = await orchestrator.db_repo.get_tasks_by_plan(plan.id) if plan else []
                n_done = sum(1 for t in tasks if t.status == TaskStatus.completed)
                n_total = len(tasks)
                console.print(
                    f"[yellow]Interrupted session[/yellow] [dim]{sess.id}[/dim] — "
                    f"{n_done}/{n_total} tasks completed. [r]esume, [a]bandon, or [s]kip?"
                )
                choice_raw = await async_input("[r/a/s] ")
                c = choice_raw.strip().lower()
                if c in ("r", "resume"):
                    try:
                        await orchestrator.resume(sess)
                        console.print("[green]Resumed session completed.[/green]")
                    except ZenoError as e:
                        cli_display.print_error(console, e)
                elif c in ("a", "abandon"):
                    try:
                        await orchestrator.abandon_session(sess)
                        console.print("Session abandoned.")
                    except ZenoError as e:
                        cli_display.print_error(console, e)
        except ZenoError as e:
            cli_display.print_error(console, e)

        while True:
            raw = await async_input("> ")
            parsed = parse_input(raw)

            if isinstance(parsed, SlashCommand):
                if parsed.command == "quit":
                    await orchestrator.teardown()
                    console.print("Goodbye.")
                    return
                if parsed.command == "status":
                    sid: UUID | None = None
                    st: OrchestratorState | None = None
                    if orchestrator.current_session is not None:
                        sid = orchestrator.current_session.id
                        st = await orchestrator.db_repo.get_orchestrator_state(sid)
                    cli_display.print_status(
                        console,
                        session_id=sid,
                        orchestrator_state=st,
                        working_directory=orchestrator.working_directory,
                        execution_mode=execution_mode,
                    )
                elif parsed.command == "help":
                    cli_display.print_help(console)
                continue

            if isinstance(parsed, TaskInput):
                if not parsed.text.strip():
                    continue
                await orchestrator.run(parsed.text)
    except (KeyboardInterrupt, asyncio.CancelledError):
        # Ensure immediate exit on Ctrl+C.
        try:
            await orchestrator.teardown()
        finally:
            console.print("Goodbye.")
        return


def run() -> None:
    app()


if __name__ == "__main__":
    run()
