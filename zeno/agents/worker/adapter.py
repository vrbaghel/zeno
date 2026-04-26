from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import logging
import traceback

from zeno.agents.models import (
    AgentContext,
    WorkerMetrics,
    WorkerResponse,
    WorkerTerminateResponse,
    WORKER_RESPONSE_SCHEMA,
)
from zeno.agents.worker.composer import build_system_prompt
from zeno.orchestrator.errors import ParseError
from zeno.orchestrator.errors import WorkerTerminationError
from zeno.orchestrator.errors import map_sdk_error  # added in Migration 3

logger = logging.getLogger(__name__)

_SDK_IMPORT_ERROR: str | None = None

try:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
    try:  # pragma: no cover
        from claude_agent_sdk.types import AssistantMessage, ResultMessage  # type: ignore
    except Exception:  # pragma: no cover
        from claude_agent_sdk.messages import AssistantMessage, ResultMessage  # type: ignore
except Exception as e:  # pragma: no cover
    _SDK_IMPORT_ERROR = "".join(
        traceback.format_exception(type(e), e, e.__traceback__)
    ).strip()
    ClaudeAgentOptions = None  # type: ignore[assignment]
    ClaudeSDKClient = None  # type: ignore[assignment]
    AssistantMessage = ResultMessage = None  # type: ignore[assignment]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WorkerAdapter:
    def __init__(self, working_directory: str) -> None:
        self.working_directory = working_directory

    async def dispatch(self, *, task, agent, chroma_context: AgentContext) -> tuple[WorkerResponse, WorkerMetrics]:
        """
        One-shot SDK dispatch for a worker task.

        `task` and `agent` are expected to be DB models (DbTask/DbAgent) or lookalikes
        providing at least:
        - task.description
        - task.agent_type
        - task.agent_responsibilities
        """
        if ClaudeSDKClient is None or ClaudeAgentOptions is None:
            raise map_sdk_error(
                ImportError(
                    "claude-agent-sdk is not available"
                    + (f"\nSDK import error:\n{_SDK_IMPORT_ERROR}" if _SDK_IMPORT_ERROR else "")
                )
            )

        agent_type = str(getattr(agent, "type", "") or getattr(task, "agent_type", "") or "")
        agent_responsibilities = getattr(task, "agent_responsibilities", None)

        system_prompt = build_system_prompt(
            agent_type=agent_type,
            agent_responsibilities=agent_responsibilities,
            chroma_context=chroma_context,
            working_directory=self.working_directory,
        )
        logger.info(
            "Worker dispatch started | agent_type=%s model=%s",
            agent_type,
            "claude-haiku-4-5-20251001",
        )
        logger.debug("Worker prompt length | chars=%d", len(system_prompt))

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
            permission_mode="acceptEdits",
            model="claude-haiku-4-5-20251001",
            max_turns=30,
            output_format=WORKER_RESPONSE_SCHEMA,
            cwd=self.working_directory,
        )

        queued_at = _now()
        first_token_at: datetime | None = None

        try:
            async with ClaudeSDKClient(options=options) as client:
                await client.query(str(getattr(task, "description", "") or ""))

                async for msg in client.receive_response():
                    if AssistantMessage is not None and isinstance(msg, AssistantMessage):
                        if first_token_at is None and getattr(msg, "content", None):
                            first_token_at = _now()
                        logger.debug("Worker SDK message | type=%s", type(msg).__name__)
                        continue

                    if ResultMessage is not None and isinstance(msg, ResultMessage):
                        completed_at = _now()
                        output = getattr(msg, "structured_output", None)
                        if isinstance(output, str):
                            output = self._clean_output(output)
                        if not isinstance(output, dict):
                            raise ParseError(
                                "Worker structured_output is not an object",
                                detail=repr(output)[:500],
                            )

                        response_type = output.get("type")
                        if response_type == "terminate":
                            term = WorkerTerminateResponse(**output)
                            logger.warning("Worker terminated | reason=%s", term.reason)
                            raise WorkerTerminationError(term.reason)
                        if response_type == "success":
                            response = WorkerResponse(**output)
                        else:
                            raise ParseError("Unknown worker response type", detail=str(response_type))

                        usage: dict[str, Any] = getattr(msg, "usage", None) or {}
                        input_tokens = usage.get("input_tokens")
                        output_tokens = usage.get("output_tokens")
                        cache_read = usage.get("cache_read_input_tokens")
                        cache_create = usage.get("cache_creation_input_tokens")

                        total_tokens = None
                        if isinstance(input_tokens, int) and isinstance(output_tokens, int):
                            total_tokens = input_tokens + output_tokens

                        latency_ms = int((completed_at - queued_at).total_seconds() * 1000)
                        ttfb_ms = None
                        if first_token_at is not None:
                            ttfb_ms = int((first_token_at - queued_at).total_seconds() * 1000)

                        model = None
                        try:
                            # Some SDK versions provide model on AssistantMessage only; fall back to result fields if any.
                            model = getattr(msg, "model", None) or None
                        except Exception:
                            model = None

                        metrics = WorkerMetrics(
                            queued_at=queued_at,
                            first_token_at=first_token_at,
                            completed_at=completed_at,
                            latency_ms=latency_ms,
                            time_to_first_token_ms=ttfb_ms,
                            input_tokens=input_tokens if isinstance(input_tokens, int) else None,
                            output_tokens=output_tokens if isinstance(output_tokens, int) else None,
                            total_tokens=total_tokens,
                            cache_read_tokens=cache_read if isinstance(cache_read, int) else None,
                            cache_creation_tokens=cache_create if isinstance(cache_create, int) else None,
                            cost_usd=getattr(msg, "total_cost_usd", None),
                            model=model,
                            num_turns=getattr(msg, "num_turns", None),
                        )

                        logger.info(
                            "Worker complete | tokens=%s cost=%s latency_ms=%s turns=%s",
                            getattr(metrics, "total_tokens", None),
                            getattr(metrics, "cost_usd", None),
                            getattr(metrics, "latency_ms", None),
                            getattr(metrics, "num_turns", None),
                        )
                        return response, metrics

        except Exception as e:
            logger.error("Worker dispatch failed | err=%s", repr(e))
            raise map_sdk_error(e) from e

        raise ParseError("Worker did not produce a ResultMessage")

    def _clean_output(self, output: str) -> dict[str, Any]:
        """
        Strip common markdown fences from structured output.
        """
        s = output.strip()
        if s.startswith("```"):
            # Drop opening fence (optionally ```json) and closing fence.
            lines = s.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            s = "\n".join(lines).strip()
        try:
            import json

            parsed = json.loads(s)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        return {"type": "terminate", "reason": "Worker returned non-JSON structured output"}  # fallback

