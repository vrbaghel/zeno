from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from zeno.agents.base import BaseAgentAdapter, _assemble_metrics, utc_now
from zeno.agents.models import (
    AdaptorError,
    AdaptorErrorCode,
    AdaptorMessage,
    AdaptorMetrics,
    AdaptorRequest,
    AdaptorResponse,
    AdaptorResponsePayload,
    AdaptorResponseStatus,
    AgentResponse,
)


def _serialize_request_to_prompt(request: AdaptorRequest) -> str:
    parts: list[str] = []
    if request.payload.system:
        parts.append("System:\n" + request.payload.system.strip())

    for m in request.payload.messages:
        parts.append(f"{m.role}:\n{m.content.strip()}")

    parts.append(
        """
You must respond only in this JSON format. Do not include
any text outside of this JSON block:

{
  "status": "success | error | truncated",
  "payload": {
    "messages": [{ "role": "assistant", "content": "..." }]
  },
  "artifacts": {
    "created": [],
    "updated": [],
    "deleted": []
  },
  "log": {
    "summary": "...",
    "decisions": ["..."],
    "assumptions": ["..."],
    "dependencies": ["..."],
    "open_issues": ["..."],
    "room": "..."
  }
}
""".strip()
    )

    return "\n\n".join(parts).strip() + "\n"


def _ms_between(a: datetime | None, b: datetime | None) -> int | None:
    if a is None or b is None:
        return None
    return int((b - a).total_seconds() * 1000)


@dataclass(frozen=True)
class GeminiAdaptorConfig:
    model: str | None = None
    sp_model_path: str | None = None  # sentencepiece model path for exact counts


class GeminiAdaptor(BaseAgentAdapter):
    name = "gemini"
    provider = "gemini"
    mode = "adapter"
    deviation = "±15%"

    def __init__(self, *, config: GeminiAdaptorConfig | None = None) -> None:
        self._config = config or GeminiAdaptorConfig()

    def probe(self) -> bool:
        return self._which("gemini")

    def adaptor_info(self) -> dict:
        return {
            "name": self.name,
            "provider": self.provider,
            "mode": self.mode,
            "model": self._config.model,
        }

    async def dispatch(
        self, request: AdaptorRequest
    ) -> tuple[AdaptorResponse, AdaptorMetrics] | AdaptorError:
        queued_at = utc_now()
        agent_id = request.agent_id

        if not self.probe():
            return AdaptorError(
                request_id=request.id,
                agent_id=agent_id,
                code=AdaptorErrorCode.ADAPTOR_NOT_FOUND,
                message="Gemini CLI not found on PATH (expected `gemini`).",
                recoverable=False,
            )

        prompt = _serialize_request_to_prompt(request)

        try:
            process = await self._spawn("gemini")
        except Exception as e:
            return AdaptorError(
                request_id=request.id,
                agent_id=agent_id,
                code=AdaptorErrorCode.ADAPTOR_SPAWN_FAILED,
                message=f"Failed to spawn gemini: {e}",
                recoverable=True,
            )

        dispatched_at = utc_now()

        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None

        try:
            process.stdin.write(prompt.encode())
            await process.stdin.drain()
            process.stdin.close()
        except Exception as e:
            await self._terminate(process)
            return AdaptorError(
                request_id=request.id,
                agent_id=agent_id,
                code=AdaptorErrorCode.ADAPTOR_SPAWN_FAILED,
                message=f"Failed to write prompt to gemini stdin: {e}",
                recoverable=True,
            )

        timeout_s = request.timeout_seconds or 120.0

        try:
            stdout_task = self._collect(process.stdout)
            stderr_task = self._collect(process.stderr)
            stdout_res, stderr_res = await asyncio.wait_for(
                asyncio.gather(stdout_task, stderr_task),
                timeout=timeout_s,
            )
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except TimeoutError:
            await self._terminate(process)
            return AdaptorError(
                request_id=request.id,
                agent_id=agent_id,
                code=AdaptorErrorCode.ADAPTOR_TIMEOUT,
                message=f"Gemini adaptor timed out after {timeout_s:.1f}s.",
                recoverable=True,
            )
        except Exception as e:
            await self._terminate(process)
            return AdaptorError(
                request_id=request.id,
                agent_id=agent_id,
                code=AdaptorErrorCode.UNKNOWN,
                message=f"Unexpected error while running gemini: {e}",
                recoverable=True,
            )

        completed_at = utc_now()
        first_token_at = stdout_res.first_byte_at

        raw_out = stdout_res.text.strip()
        raw_err = stderr_res.text.strip()

        try:
            data = self._json_loads(_extract_json_object_strict(raw_out))
            agent_resp = AgentResponse.model_validate(data)
        except Exception as e:
            detail = raw_err or raw_out
            snippet = (detail[:500] + "…") if len(detail) > 500 else detail
            return AdaptorError(
                request_id=request.id,
                agent_id=agent_id,
                code=AdaptorErrorCode.ADAPTOR_PARSE_ERROR,
                message=f"Failed to parse gemini output as AdaptorResponse: {e}. Output: {snippet}",
                recoverable=True,
            )

        if agent_resp.log is None:
            # Logs are recoverable (useful but not required).
            print("warning: gemini response missing log")

        payload = (
            agent_resp.payload
            if agent_resp.payload.messages
            else AdaptorResponsePayload(messages=[AdaptorMessage(role="assistant", content="")])
        )
        resp = AdaptorResponse(
            request_id=request.id,
            session_id=request.session_id,
            agent_id=agent_id,
            status=AdaptorResponseStatus(agent_resp.status),
            payload=payload,
            artifacts=agent_resp.artifacts,
        )

        metrics = _assemble_metrics(
            agent_id=agent_id,
            provider=self.provider,
            mode=self.mode,
            model=self._config.model,
            queued_at=queued_at,
            dispatched_at=dispatched_at,
            first_token_at=first_token_at,
            completed_at=completed_at,
            prompt_text=prompt,
            output_text=raw_out,
            artifact_created_count=len(resp.artifacts.created),
            artifact_updated_count=len(resp.artifacts.updated),
            artifact_deleted_count=len(resp.artifacts.deleted),
            deviation=self.deviation,
        )

        if resp.status == AdaptorResponseStatus.truncated:
            pass

        return resp, metrics


def _extract_json_object_strict(text: str) -> str:
    # Gemini CLI can print pre/post text. Try progressively from the last '{'.
    starts = [i for i, ch in enumerate(text) if ch == "{"]
    if not starts:
        raise ValueError("no JSON object found in output")

    last_err: Exception | None = None
    end = text.rfind("}")
    if end == -1:
        raise ValueError("no JSON object found in output")

    for start in reversed(starts):
        if start >= end:
            continue
        candidate = text[start : end + 1]
        try:
            json.loads(candidate)
            return candidate
        except Exception as e:
            last_err = e
            continue

    raise ValueError(f"no valid JSON object found in output ({last_err})")

