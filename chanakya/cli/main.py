from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console

from chanakya import __version__
from chanakya.core.config import load_config
from chanakya.core.mode import (
    OperationMode,
    api_key_statuses,
    api_mode_has_any_key,
    resolve_mode,
    scan_adapters,
)
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
    mode: Optional[str] = typer.Option(
        None,
        "--mode",
        help="Operational mode: adapter or api",
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

    console = Console()

    loaded = load_config()
    cli_mode = _parse_mode(mode)
    config_mode = OperationMode.parse(loaded.settings.mode)
    active_mode, source = resolve_mode(cli_mode=cli_mode, config_mode=config_mode)

    ctx = StartupContext(
        version=__version__,
        config_found=loaded.found,
        config_path=loaded.path,
        mode=active_mode,
        mode_source=source,
    )

    if active_mode == OperationMode.adapter:
        adapters = scan_adapters()
        print_adapter_startup(console=console, ctx=ctx, adapters=adapters)
        return

    keys = api_key_statuses(loaded.settings)
    if not api_mode_has_any_key(loaded.settings):
        print_api_missing_keys_error(console=console, ctx=ctx, keys=keys)
        raise typer.Exit(code=1)

    # Phase 1: if API mode is usable, just report readiness and exit.
    # (No agents, no prompts yet.)
    print_api_ready(console=console, ctx=ctx)


def run() -> None:
    # Convenience entrypoint for tools/tests.
    app()


if __name__ == "__main__":
    run()

