from __future__ import annotations

import asyncio
import os
from typing import Optional
from uuid import UUID

import typer
from rich.console import Console

from chanakya import __version__
from chanakya.arthashastra.models import (
    AdaptorMessage,
    AdaptorRequest,
    AdaptorRequestPayload,
    CheckpointContent,
)
from chanakya.arthashastra.registry import AdaptorRegistry
from chanakya.cli import display as cli_display
from chanakya.cli.input import SlashCommand, TaskInput, async_input, parse_input
from chanakya.core.config import load_config
from chanakya.core.enums import ExecutionMode, OrchestratorState
from chanakya.core.mode import (
    OperationMode,
    api_key_statuses,
    api_mode_has_any_key,
    resolve_mode,
    scan_adapters,
)
from chanakya.orchestrator.core import OrchestratorCore
from chanakya.orchestrator.errors import ChanakyaError
from chanakya.utils.display import (
    StartupContext,
    print_adapter_startup,
    print_api_ready,
    print_api_missing_keys_error,
)

app = typer.Typer(add_completion=False, no_args_is_help=False)


def _parse_mode(value: Optional[str]) -> Optional[OperationMode]:
    if value is None:
        return None
    parsed = OperationMode.parse(value)
    if parsed is None:
        raise typer.BadParameter("Mode must be one of: adapter, api")
    return parsed


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    yolo: bool = typer.Option(False, "--yolo", help="YOLO execution mode (default is HITL)."),
    mode: Optional[str] = typer.Option(
        None,
        "--mode",
        help="Operation mode: adapter or api",
    ),
    version: bool = typer.Option(
        False,
        "--version",
        help="Show version and exit",
        is_eager=True,
    ),
) -> None:
    if version:
        typer.echo(f"Chanakya v{__version__}")
        raise typer.Exit()

    if ctx.invoked_subcommand is not None:
        return

    console = Console()
    loaded = load_config()
    cli_mode = _parse_mode(mode)
    config_mode = OperationMode.parse(loaded.settings.mode)
    active_mode, source = resolve_mode(cli_mode=cli_mode, config_mode=config_mode)

    ctx_obj = StartupContext(
        version=__version__,
        config_found=loaded.found,
        config_path=loaded.path,
        mode=active_mode,
        mode_source=source,
    )

    if active_mode == OperationMode.adapter:
        adapters = scan_adapters()
        print_adapter_startup(console=console, ctx=ctx_obj, adapters=adapters)
    else:
        keys = api_key_statuses(loaded.settings)
        if not api_mode_has_any_key(loaded.settings):
            print_api_missing_keys_error(console=console, ctx=ctx_obj, keys=keys)
            raise typer.Exit(code=1)
        print_api_ready(console=console, ctx=ctx_obj)

    execution_mode = ExecutionMode.YOLO if yolo else ExecutionMode.HITL

    asyncio.run(
        _interactive_main(
            console=console,
            operation_mode=active_mode,
            execution_mode=execution_mode,
        )
    )


async def _interactive_main(
    *,
    console: Console,
    operation_mode: OperationMode,
    execution_mode: ExecutionMode,
) -> None:
    cwd = os.getcwd()

    async def hitl_callback(content: CheckpointContent) -> str:
        return await cli_display.print_checkpoint(console, content)

    orchestrator = OrchestratorCore(
        execution_mode=execution_mode,
        operation_mode=operation_mode,
        working_directory=cwd,
        hitl_callback=hitl_callback if execution_mode == ExecutionMode.HITL else None,
    )

    try:
        await orchestrator.initialize_runtime()
    except ChanakyaError as e:
        cli_display.print_error(console, e)
        raise typer.Exit(code=1) from e

    registry = AdaptorRegistry.discover()
    adapter_label = "gemini" if "gemini" in registry.available() else "(none)"

    cli_display.print_welcome(
        console,
        version=__version__,
        execution_mode=execution_mode,
        operation_mode=operation_mode,
        adapter_label=adapter_label,
        wing_name=orchestrator.wing_name,
    )

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


@app.command("test-adaptor")
def test_adaptor(
    prompt: Optional[str] = typer.Option(
        None,
        "--prompt",
        help="Custom user prompt to dispatch via the Gemini adaptor.",
    ),
) -> None:
    """
    Temporary end-to-end adaptor test command (Phase 2).
    """

    console = Console()
    registry = AdaptorRegistry.discover()

    if "gemini" not in registry.available():
        console.print("Error: Gemini adaptor not found (gemini CLI not on PATH).")
        raise typer.Exit(code=1)

    user_prompt = (
        prompt
        or "Create a file called hello.txt with the content Hello from Chanakya"
    )

    request = AdaptorRequest(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        session_id=UUID("00000000-0000-0000-0000-000000000002"),
        agent_id="test-agent",
        payload=AdaptorRequestPayload(
            system="You are a helpful assistant.",
            messages=[AdaptorMessage(role="user", content=user_prompt)],
            tools=[],
        ),
        timeout_seconds=30.0,
    )

    adaptor = registry.get("gemini")
    result = asyncio.run(adaptor.dispatch(request))

    if isinstance(result, tuple):
        response, metrics = result
        cli_display.print_adaptor_result(console, response=response, metrics=metrics)
        return

    cli_display.print_adaptor_result(console, error=result)
    raise typer.Exit(code=1)


def run() -> None:
    app()


if __name__ == "__main__":
    run()
