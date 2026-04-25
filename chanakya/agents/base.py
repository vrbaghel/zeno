from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from shutil import which
from typing import Any, AsyncIterator

import tiktoken

from chanakya.agents.models import (
    AdaptorArtifactMetrics,
    AdaptorError,
    AdaptorMetrics,
    AdaptorRequest,
    AdaptorResponse,
    AdaptorTimingMetrics,
    AdaptorTokenMetrics,
)

PROVIDER_FLAGS: dict[str, list[str]] = {
    "gemini": ["--yolo"],
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class StreamReadResult:
    text: str
    first_byte_at: datetime | None


async def _spawn_process(cmd: str, args: list[str]) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        cmd,
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def _terminate(process: asyncio.subprocess.Process, *, grace_seconds: float = 2.0) -> None:
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
        return


async def _read_stream(stream: asyncio.StreamReader) -> AsyncIterator[tuple[str, datetime | None]]:
    first_byte_at: datetime | None = None
    while True:
        chunk = await stream.readline()
        if not chunk:
            break
        if first_byte_at is None and len(chunk) > 0:
            first_byte_at = utc_now()
        yield chunk.decode(errors="replace"), first_byte_at


async def _collect_stream(stream: asyncio.StreamReader) -> StreamReadResult:
    parts: list[str] = []
    first_byte_at: datetime | None = None
    async for line, fb in _read_stream(stream):
        if first_byte_at is None and fb is not None:
            first_byte_at = fb
        parts.append(line)
    return StreamReadResult(text="".join(parts), first_byte_at=first_byte_at)


async def _read_until_complete(stream: asyncio.StreamReader) -> str:
    accumulator: list[str] = []
    depth = 0
    started = False

    async for line, _fb in _read_stream(stream):
        decoded = line.rstrip("\n")
        accumulator.append(decoded)

        for ch in decoded:
            if ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1

        if started and depth == 0:
            return "\n".join(accumulator).strip()

    raise RuntimeError("process exited before complete JSON received")


def _count_tokens(text: str) -> int:
    enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(text))


def _ms_between(a: datetime | None, b: datetime | None) -> int | None:
    if a is None or b is None:
        return None
    return int((b - a).total_seconds() * 1000)


def _assemble_metrics(
    *,
    agent_id: str,
    provider: str,
    mode: str,
    model: str | None,
    queued_at: datetime | None,
    dispatched_at: datetime | None,
    first_token_at: datetime | None,
    completed_at: datetime | None,
    prompt_text: str,
    output_text: str,
    artifact_created_count: int,
    artifact_updated_count: int,
    artifact_deleted_count: int,
    deviation: str | None = None,
) -> AdaptorMetrics:
    input_tok = _count_tokens(prompt_text)
    output_tok = _count_tokens(output_text)

    timing = AdaptorTimingMetrics(
        queued_at=queued_at,
        dispatched_at=dispatched_at,
        first_token_at=first_token_at,
        completed_at=completed_at,
        latency_ms=_ms_between(queued_at, completed_at),
        time_to_first_token_ms=_ms_between(dispatched_at, first_token_at),
    )

    tokens = AdaptorTokenMetrics(
        input=input_tok,
        output=output_tok,
        total=input_tok + output_tok,
        deviation=deviation,
    )

    artifacts = AdaptorArtifactMetrics(
        created_count=artifact_created_count,
        updated_count=artifact_updated_count,
        deleted_count=artifact_deleted_count,
    )

    return AdaptorMetrics(
        timing=timing,
        tokens=tokens,
        artifacts=artifacts,
        agent_id=agent_id,
        mode=mode,  # type: ignore[arg-type]
        provider=provider,  # type: ignore[arg-type]
        model=model,
    )


class BaseAgentAdapter(ABC):
    name: str
    provider: str
    mode: str

    @abstractmethod
    def probe(self) -> bool:
        """
        Return True if this adapter can run on the current machine.
        Never throws.
        """

    @abstractmethod
    async def dispatch(
        self, request: AdaptorRequest
    ) -> tuple[AdaptorResponse, AdaptorMetrics] | AdaptorError:
        """
        Dispatch a request and return either:
        - (AdaptorResponse, AdaptorMetrics)
        - AdaptorError
        """

    @abstractmethod
    def adaptor_info(self) -> dict:
        """
        Static metadata for registry and diagnostics.
        """

    @staticmethod
    def _which(binary: str) -> bool:
        try:
            return which(binary) is not None
        except Exception:
            return False

    @staticmethod
    async def _spawn(provider: str) -> asyncio.subprocess.Process:
        flags = PROVIDER_FLAGS.get(provider, [])
        return await _spawn_process(provider, flags)

    @staticmethod
    async def _collect(stream: asyncio.StreamReader) -> StreamReadResult:
        return await _collect_stream(stream)

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        await _terminate(process)

    @staticmethod
    def _json_loads(text: str) -> dict[str, Any]:
        obj = json.loads(text)
        if not isinstance(obj, dict):
            raise ValueError("expected JSON object")
        return obj

