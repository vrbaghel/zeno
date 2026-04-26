from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from zeno.core.mode import AdapterStatus, ApiKeyStatus, ModeSource, OperationMode


@dataclass(frozen=True)
class StartupContext:
    version: str
    config_found: bool
    config_path: Path
    mode: OperationMode
    mode_source: ModeSource


def _mode_source_label(source: ModeSource) -> str:
    if source == "default":
        return "default"
    if source == "config":
        return "from config"
    if source == "--mode":
        return "from --mode flag"
    return source


def _checkmark(found: bool) -> str:
    return "✓" if found else "✗"


def print_adapter_startup(
    *,
    console: Console,
    ctx: StartupContext,
    adapters: list[AdapterStatus],
) -> None:
    console.print(f"Zeno v{ctx.version}")
    console.print("─" * 34)

    if not ctx.config_found:
        console.print("No configuration found. Starting with defaults.")

    console.print(f"Mode: {ctx.mode.value} ({_mode_source_label(ctx.mode_source)})")
    console.print("Scanning for CLI adapters...")
    for a in adapters:
        if a.found and a.path:
            console.print(f"  {_checkmark(True)} {a.name:<7} found at {a.path}")
        else:
            console.print(f"  {_checkmark(False)} {a.name:<7} not found")

    console.print("─" * 34)
    if not ctx.config_found:
        console.print(f"Ready. No config file at {ctx.config_path}")
    else:
        console.print("Ready.")


def print_api_missing_keys_error(
    *,
    console: Console,
    ctx: StartupContext,
    keys: list[ApiKeyStatus],
) -> None:
    console.print(f"Zeno v{ctx.version}")
    console.print("─" * 34)

    if not ctx.config_found:
        console.print("No configuration found. Starting with defaults.")

    console.print(f"Mode: {ctx.mode.value} ({_mode_source_label(ctx.mode_source)})")
    console.print("Error: API mode requires at least one API key to be configured.")
    for k in keys:
        console.print(f"  {k.name:<13} → {'set' if k.set else 'not set'}")

    console.print()
    console.print("Set a key with: zeno config set openai-key <key>")
    console.print("─" * 34)


def print_api_ready(
    *,
    console: Console,
    ctx: StartupContext,
) -> None:
    console.print(f"Zeno v{ctx.version}")
    console.print("─" * 34)

    if not ctx.config_found:
        console.print("No configuration found. Starting with defaults.")

    console.print(f"Mode: {ctx.mode.value} ({_mode_source_label(ctx.mode_source)})")
    console.print("─" * 34)
    console.print("Ready.")

