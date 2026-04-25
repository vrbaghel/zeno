from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from chanakya.agents.base import _read_until_complete, utc_now
from chanakya.agents.models import (
    AgentContext,
    ClarificationInput,
    ExecutionPlanResponse,
    LeadAgentResponse,
    validate_lead_response,
)
from chanakya.core.enums import ExecutionMode, LeadAgentStage
from chanakya.orchestrator.errors import DispatchError, ParseError, ValidationError


class LeadAgentContext(BaseModel):
    session_id: str
    raw_input: str
    mode: ExecutionMode
    stage: LeadAgentStage
    working_directory: str
    existing_rooms: list[str] = Field(default_factory=list)
    agent_context: AgentContext

    current_plan: ExecutionPlanResponse | None = None
    completed_tasks: list[str] | None = None
    revision_reason: str | None = None


@dataclass
class _Timestamps:
    dispatched_at: datetime | None = None
    completed_at: datetime | None = None


class LeadAgentAdapter:
    def __init__(self, *, timeout_seconds: float = 60.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._process: asyncio.subprocess.Process | None = None
        self._ts = _Timestamps()

    async def start(self, prompt: str) -> None:
        try:
            self._process = await asyncio.create_subprocess_exec(
                "gemini",
                "--yolo",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as e:
            raise DispatchError(f"Failed to spawn gemini: {e}") from e

        assert self._process.stdin is not None
        assert self._process.stdout is not None

        try:
            self._process.stdin.write(prompt.encode())
            await self._process.stdin.drain()
        except Exception as e:
            await _terminate_process(self._process)
            raise DispatchError(f"Failed to write prompt to gemini stdin: {e}") from e

        self._ts.dispatched_at = utc_now()

    async def read_response(self) -> LeadAgentResponse:
        if self._process is None:
            raise DispatchError("Process not started")

        try:
            text = await asyncio.wait_for(self._read_until_complete(), timeout=self.timeout_seconds)
        except TimeoutError as e:
            await self.terminate()
            raise DispatchError(f"Response timed out after {self.timeout_seconds:.1f}s") from e

        try:
            data = json.loads(text)
        except Exception as e:
            raise ParseError(f"Invalid JSON from lead agent: {e}", detail=_snippet(text)) from e

        if not isinstance(data, dict):
            raise ParseError("Lead agent output must be a JSON object", detail=_snippet(text))

        try:
            resp = LeadAgentResponse.model_validate(data)
        except Exception as e:
            raise ParseError(f"Failed to parse LeadAgentResponse: {e}", detail=_snippet(text)) from e

        errors = validate_lead_response(resp)
        if errors:
            raise ValidationError("LeadAgentResponse validation failed", detail="\n".join(errors))

        self._ts.completed_at = utc_now()
        return resp

    async def send_answers(self, answers: ClarificationInput) -> None:
        if self._process is None:
            raise DispatchError("Process not started")
        if self._process.stdin is None:
            raise DispatchError("Process stdin not available")

        payload = answers.model_dump()
        data = json.dumps(payload, ensure_ascii=False)
        try:
            self._process.stdin.write((data + "\n").encode())
            await self._process.stdin.drain()
        except Exception as e:
            await self.terminate()
            raise DispatchError(f"Failed to write clarification answers to stdin: {e}") from e

    async def terminate(self) -> None:
        if self._process is None:
            return

        try:
            if self._process.stdin is not None:
                self._process.stdin.close()
        except Exception:
            pass

        try:
            await asyncio.wait_for(self._process.wait(), timeout=2.0)
        except TimeoutError:
            await _terminate_process(self._process)
        finally:
            self._ts.completed_at = utc_now()
            self._process = None

    async def _read_until_complete(self) -> str:
        if self._process is None or self._process.stdout is None:
            raise DispatchError("Process not started")
        try:
            return await _read_until_complete(self._process.stdout)
        except Exception as e:
            raise DispatchError("Process exited before complete JSON received", detail=str(e)) from e


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=2.0)
        return
    except TimeoutError:
        pass
    process.kill()
    try:
        await asyncio.wait_for(process.wait(), timeout=2.0)
    except TimeoutError:
        return


def _snippet(text: str, *, limit: int = 800) -> str:
    s = text.strip()
    return (s[:limit] + "…") if len(s) > limit else s

