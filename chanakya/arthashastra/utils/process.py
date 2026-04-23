from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class StreamReadResult:
    text: str
    first_byte_at: datetime | None


async def spawn(cmd: str, args: list[str]) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        cmd,
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def read_stream(
    stream: asyncio.StreamReader,
) -> AsyncIterator[tuple[str, datetime | None]]:
    first_byte_at: datetime | None = None
    while True:
        chunk = await stream.readline()
        if not chunk:
            break
        if first_byte_at is None and len(chunk) > 0:
            first_byte_at = utc_now()
        yield chunk.decode(errors="replace"), first_byte_at


async def collect_stream(stream: asyncio.StreamReader) -> StreamReadResult:
    parts: list[str] = []
    first_byte_at: datetime | None = None
    async for line, fb in read_stream(stream):
        if first_byte_at is None and fb is not None:
            first_byte_at = fb
        parts.append(line)
    return StreamReadResult(text="".join(parts), first_byte_at=first_byte_at)


async def terminate(process: asyncio.subprocess.Process, *, grace_seconds: float = 2.0) -> None:
    if process.returncode is not None:
        return

    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=grace_seconds)
        return
    except TimeoutError:
        pass

    process.kill()
    try:
        await asyncio.wait_for(process.wait(), timeout=grace_seconds)
    except TimeoutError:
        # If the platform refuses to kill, there's nothing safe left to do here.
        return

