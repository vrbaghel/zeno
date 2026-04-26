from __future__ import annotations

import asyncio
import json
import logging
import traceback
from datetime import datetime, timezone
from typing import Any

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
from zeno.orchestrator.errors import ZenoError
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

_RECONCILE_MAX_ATTEMPTS = 2


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _git_status_porcelain(worktree_path: str) -> str:
    """Return raw `git status --porcelain` output for the worktree, empty string on failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "status", "--porcelain",
            cwd=worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out_b, _ = await proc.communicate()
        return (out_b or b"").decode("utf-8", errors="replace").strip()
    except Exception as exc:
        logger.warning("git status failed during reconciliation | err=%s", exc)
        return ""


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

        On ParseError (bad structured_output), attempts up to _RECONCILE_MAX_ATTEMPTS
        cheap reconciliation calls before re-raising.
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
        last_parse_error: ParseError | None = None

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

                        # --- try to parse; on failure, attempt reconciliation ---
                        parse_err: ParseError | None = None
                        if not isinstance(output, dict):
                            parse_err = ParseError(
                                "Worker structured_output is not an object",
                                detail=repr(output)[:500],
                            )
                        else:
                            response_type = output.get("type")
                            if response_type == "terminate":
                                term = WorkerTerminateResponse(**output)
                                logger.warning("Worker terminated | reason=%s", term.reason)
                                raise WorkerTerminationError(term.reason)
                            if response_type != "success":
                                parse_err = ParseError(
                                    "Unknown worker response type", detail=str(response_type)
                                )

                        if parse_err is not None:
                            logger.warning(
                                "Worker bad structured_output | err=%s — attempting reconciliation",
                                parse_err,
                            )
                            task_description = str(getattr(task, "description", "") or "")
                            task_title = str(getattr(task, "title", task_description[:60]) or "")
                            reconciled = await self._reconcile_output(
                                task_description=task_description,
                                task_title=task_title,
                            )
                            if reconciled is not None:
                                response = reconciled
                            else:
                                last_parse_error = parse_err
                                raise parse_err
                        else:
                            response = WorkerResponse(**output)  # type: ignore[arg-type]

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
            if isinstance(e, ZenoError):
                raise
            raise map_sdk_error(e) from e

        raise ParseError("Worker did not produce a ResultMessage")

    async def _reconcile_output(
        self,
        *,
        task_description: str,
        task_title: str,
    ) -> WorkerResponse | None:
        """
        Attempt to recover a valid WorkerResponse when the main dispatch produced
        bad structured_output.

        Runs up to _RECONCILE_MAX_ATTEMPTS no-tools, single-turn calls that show
        the model the git status of the worktree and ask it to emit only the JSON
        summary of what it just did.

        Returns a WorkerResponse on success, or None if all attempts fail.
        """
        if ClaudeSDKClient is None or ClaudeAgentOptions is None:
            return None

        git_status = await _git_status_porcelain(self.working_directory)
        files_context = (
            f"Files changed in the working directory (git status --porcelain):\n{git_status}"
            if git_status
            else "No file changes detected in the working directory."
        )

        reconcile_prompt = (
            "You previously ran as a worker agent and completed the following task:\n\n"
            f"Task: {task_description}\n\n"
            f"{files_context}\n\n"
            "Your failed before returning a valid response. Please now produce ONLY the structured "
            "output for the work you completed. Do not perform any more file operations. "
        )

        for attempt in range(1, _RECONCILE_MAX_ATTEMPTS + 1):
            logger.info(
                "Reconciliation attempt %d/%d | worktree=%s",
                attempt,
                _RECONCILE_MAX_ATTEMPTS,
                self.working_directory,
            )
            try:
                options = ClaudeAgentOptions(
                    system_prompt=(
                        "You are a worker agent that completed a task. "
                        "Your only job now is to emit a valid response "
                        "describing what you did"
                    ),
                    allowed_tools=[],
                    permission_mode="acceptEdits",
                    model="claude-haiku-4-5-20251001",
                    max_turns=1,
                    output_format=WORKER_RESPONSE_SCHEMA,
                    cwd=self.working_directory,
                )
                async with ClaudeSDKClient(options=options) as client:
                    await client.query(reconcile_prompt)
                    async for msg in client.receive_response():
                        if ResultMessage is not None and isinstance(msg, ResultMessage):
                            output = getattr(msg, "structured_output", None)
                            if isinstance(output, str):
                                output = self._clean_output(output)
                            if not isinstance(output, dict):
                                logger.warning("Reconciliation attempt %d: non-dict output", attempt)
                                break
                            response_type = output.get("type")
                            if response_type == "success":
                                response = WorkerResponse(**output)
                                logger.info("Reconciliation succeeded on attempt %d", attempt)
                                return response
                            if response_type == "terminate":
                                logger.warning(
                                    "Reconciliation attempt %d: agent returned terminate | reason=%s",
                                    attempt,
                                    output.get("reason"),
                                )
                                break
                            logger.warning(
                                "Reconciliation attempt %d: unknown type=%s", attempt, response_type
                            )
                            break
            except Exception as exc:
                logger.warning("Reconciliation attempt %d failed | err=%s", attempt, repr(exc))

        logger.error(
            "Reconciliation exhausted %d attempts — giving up | worktree=%s",
            _RECONCILE_MAX_ATTEMPTS,
            self.working_directory,
        )
        return None

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
            parsed = json.loads(s)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        return {"type": "terminate", "reason": "Worker returned non-JSON structured output"}  # fallback
