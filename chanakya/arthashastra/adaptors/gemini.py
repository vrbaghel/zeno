from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from shutil import which
from typing import Any

from chanakya.arthashastra.base import BaseAdaptor
from chanakya.arthashastra.models import (
    AdaptorArtifactMetrics,
    AdaptorError,
    AdaptorErrorCode,
    AdaptorMessage,
    AdaptorMetrics,
    AdaptorRequest,
    AdaptorResponse,
    AdaptorResponsePayload,
    AdaptorResponseStatus,
    AdaptorTimingMetrics,
    AdaptorTokenMetrics,
)
from chanakya.arthashastra.utils.process import collect_stream, spawn, terminate, utc_now
from chanakya.arthashastra.utils.tokenizer import count as count_tokens


def _ms_between(a: datetime | None, b: datetime | None) -> int | None:
    if a is None or b is None:
        return None
    return int((b - a).total_seconds() * 1000)


def _serialize_request_to_prompt(request: AdaptorRequest) -> str:
    parts: list[str] = []
    if request.payload.system:
        parts.append("System:\n" + request.payload.system.strip())

    for m in request.payload.messages:
        parts.append(f"{m.role}:\n{m.content.strip()}")

    parts.append(
        """
You must respond only in the following JSON format. Do not include
any text outside of this JSON block:

{
  "id": "<uuid>",
  "request_id": "<request_id>",
  "session_id": "<session_id>",
  "agent_id": "<agent_id>",
  "status": "success | error | truncated",
  "created_at": "<utc timestamp>",
  "payload": {
    "messages": [{ "role": "assistant", "content": "..." }]
  },
  "artifacts": {
    "created": [],
    "updated": [],
    "deleted": []
  }
}
""".strip()
    )

    return "\n\n".join(parts).strip() + "\n"


def _extract_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in output")
    candidate = text[start : end + 1]
    return json.loads(candidate)


@dataclass(frozen=True)
class GeminiAdaptorConfig:
    model: str | None = None
    sp_model_path: str | None = None  # sentencepiece model path for exact counts


class GeminiAdaptor(BaseAdaptor):
    name = "gemini"
    provider = "gemini"
    mode = "adapter"
    deviation = "±15%"

    def __init__(self, *, config: GeminiAdaptorConfig | None = None) -> None:
        self._config = config or GeminiAdaptorConfig()

    def probe(self) -> bool:
        try:
            return which("gemini") is not None
        except Exception:
            return False

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

        if not self.probe():
            return AdaptorError(
                request_id=request.id,
                agent_id=request.agent_id,
                code=AdaptorErrorCode.ADAPTOR_NOT_FOUND,
                message="Gemini CLI not found on PATH (expected `gemini`).",
                recoverable=False,
            )

        prompt = _serialize_request_to_prompt(request)
        input_tok = count_tokens(prompt)

        try:
            process = await spawn("gemini", [])
        except Exception as e:
            return AdaptorError(
                request_id=request.id,
                agent_id=request.agent_id,
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
            await terminate(process)
            return AdaptorError(
                request_id=request.id,
                agent_id=request.agent_id,
                code=AdaptorErrorCode.ADAPTOR_SPAWN_FAILED,
                message=f"Failed to write prompt to gemini stdin: {e}",
                recoverable=True,
            )

        timeout_s = request.config.timeout_seconds or 60.0

        try:
            stdout_task = collect_stream(process.stdout)
            stderr_task = collect_stream(process.stderr)
            stdout_res, stderr_res = await asyncio.wait_for(
                asyncio.gather(stdout_task, stderr_task),
                timeout=timeout_s,
            )
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except TimeoutError:
            await terminate(process)
            return AdaptorError(
                request_id=request.id,
                agent_id=request.agent_id,
                code=AdaptorErrorCode.ADAPTOR_TIMEOUT,
                message=f"Gemini adaptor timed out after {timeout_s:.1f}s.",
                recoverable=True,
            )
        except Exception as e:
            await terminate(process)
            return AdaptorError(
                request_id=request.id,
                agent_id=request.agent_id,
                code=AdaptorErrorCode.UNKNOWN,
                message=f"Unexpected error while running gemini: {e}",
                recoverable=True,
            )

        completed_at = utc_now()
        first_token_at = stdout_res.first_byte_at

        raw_out = stdout_res.text.strip()
        raw_err = stderr_res.text.strip()

        try:
            data = _extract_json_object(raw_out)
            resp = AdaptorResponse.model_validate(data)
        except Exception as e:
            detail = raw_err or raw_out
            snippet = (detail[:500] + "…") if len(detail) > 500 else detail
            return AdaptorError(
                request_id=request.id,
                agent_id=request.agent_id,
                code=AdaptorErrorCode.ADAPTOR_PARSE_ERROR,
                message=f"Failed to parse gemini output as AdaptorResponse: {e}. Output: {snippet}",
                recoverable=True,
            )

        # Ensure request linkage (don't trust the model output).
        resp = resp.model_copy(
            update={
                "request_id": request.id,
                "session_id": request.session_id,
                "agent_id": request.agent_id,
                "payload": resp.payload
                if resp.payload.messages
                else AdaptorResponsePayload(
                    messages=[AdaptorMessage(role="assistant", content="")]
                ),
            }
        )

        out_tok = count_tokens(raw_out)

        tokens = AdaptorTokenMetrics(
            input=input_tok,
            output=out_tok,
            total=input_tok + out_tok,
            deviation=self.deviation,
        )

        artifact_metrics = AdaptorArtifactMetrics(
            created_count=len(resp.artifacts.created),
            updated_count=len(resp.artifacts.updated),
            deleted_count=len(resp.artifacts.deleted),
        )

        timing = AdaptorTimingMetrics(
            queued_at=queued_at,
            dispatched_at=dispatched_at,
            first_token_at=first_token_at,
            completed_at=completed_at,
            latency_ms=_ms_between(queued_at, completed_at),
            time_to_first_token_ms=_ms_between(dispatched_at, first_token_at),
        )

        metrics = AdaptorMetrics(
            timing=timing,
            tokens=tokens,
            artifacts=artifact_metrics,
            agent_id=request.agent_id,
            mode=self.mode,
            provider=self.provider,
            model=self._config.model,
        )

        if resp.status == AdaptorResponseStatus.truncated:
            # It's still a response+metrics success, but mark that truncation happened.
            pass

        return resp, metrics
