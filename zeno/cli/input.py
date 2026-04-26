from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

SlashCommandName = Literal["quit", "status", "help"]


@dataclass(frozen=True)
class SlashCommand:
    command: SlashCommandName


@dataclass(frozen=True)
class TaskInput:
    text: str


def parse_input(raw: str) -> SlashCommand | TaskInput:
    line = raw.strip()
    if not line.startswith("/"):
        return TaskInput(text=raw)

    parts = line.split(maxsplit=1)
    cmd = parts[0].lower()

    if cmd in ("/quit", "/q"):
        return SlashCommand(command="quit")
    if cmd == "/status":
        return SlashCommand(command="status")
    if cmd in ("/help", "/?"):
        return SlashCommand(command="help")

    return TaskInput(text=raw)


async def async_input(prompt: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, input, prompt)
