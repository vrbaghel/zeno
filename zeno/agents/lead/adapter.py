from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import os
import json
import logging
import traceback
from typing import Any

from pydantic import BaseModel, Field

from zeno.agents.lead.composer import compose_system_prompt, compose_user_message
from zeno.agents.models import (
    AgentContext,
    ClarificationAnswer,
    ClarificationQuestion,
    ExecutionPlanResponse,
    LEAD_AGENT_OUTPUT_SCHEMA,
    TaskStatusEntry,
    TerminateResponse,
    validate_lead_response,
)
from zeno.core.enums import ExecutionMode, LeadAgentStage
from zeno.orchestrator.errors import LeadAgentTerminationError, ParseError, ValidationError

logger = logging.getLogger(__name__)

_SDK_IMPORT_ERROR: str | None = None

try:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
    # SDK module layout varies by version:
    # - newer versions expose these via `claude_agent_sdk.types`
    # - older versions used `claude_agent_sdk.messages` / `claude_agent_sdk.content`
    try:  # pragma: no cover
        from claude_agent_sdk.types import (  # type: ignore
            AssistantMessage,
            ResultMessage,
            UserMessage,
            ToolResultBlock,
            ToolUseBlock,
        )
    except Exception:  # pragma: no cover
        from claude_agent_sdk.messages import AssistantMessage, ResultMessage, UserMessage  # type: ignore
        from claude_agent_sdk.content import ToolResultBlock, ToolUseBlock  # type: ignore
except Exception as e:  # pragma: no cover
    _SDK_IMPORT_ERROR = "".join(
        traceback.format_exception(type(e), e, e.__traceback__)
    ).strip()
    ClaudeAgentOptions = None  # type: ignore[assignment]
    ClaudeSDKClient = None  # type: ignore[assignment]
    AssistantMessage = ResultMessage = UserMessage = None  # type: ignore[assignment]
    ToolUseBlock = ToolResultBlock = None  # type: ignore[assignment]


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
    task_snapshot: list[TaskStatusEntry] | None = None
    chunk_number: int | None = None


@dataclass(frozen=True)
class _AskUserQuestion:
    questions: list[dict[str, Any]]


class LeadAgentAdapter:
    def __init__(
        self,
        *,
        execution_mode: ExecutionMode,
        working_directory: str,
        hitl_callback: Callable[[list[ClarificationQuestion]], Awaitable[list[ClarificationAnswer]]]
        | None,
    ) -> None:
        self.execution_mode = execution_mode
        self.working_directory = working_directory
        self.hitl_callback = hitl_callback
        self._session_id: str | None = None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    async def dispatch(self, context: LeadAgentContext) -> ExecutionPlanResponse:
        return await self._run(context=context, resume_session_id=self._session_id)

    async def revise(self, context: LeadAgentContext) -> ExecutionPlanResponse:
        if not self._session_id:
            raise ValidationError("Cannot revise before dispatch()", detail="session_id is not set")
        return await self._run(context=context, resume_session_id=self._session_id)

    async def continue_plan(self, context: LeadAgentContext) -> ExecutionPlanResponse:
        if not self._session_id:
            raise ValidationError("Cannot continue before dispatch()", detail="session_id is not set")
        return await self._run(context=context, resume_session_id=self._session_id)

    async def _run(self, *, context: LeadAgentContext, resume_session_id: str | None) -> ExecutionPlanResponse:
        if ClaudeSDKClient is None or ClaudeAgentOptions is None:
            raise ValidationError(
                "claude-agent-sdk is not available in this environment",
                detail=(
                    "Install claude-agent-sdk and ensure it is importable.\n"
                    + (f"SDK import error:\n{_SDK_IMPORT_ERROR}" if _SDK_IMPORT_ERROR else "")
                ).strip(),
            )

        send_prompt_on_resume = os.getenv("ZENO_LEAD_RESUME_SEND_PROMPT", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        system_prompt: str | None = None
        if resume_session_id is None:
            system_prompt = compose_system_prompt(self.execution_mode)
        elif send_prompt_on_resume:
            system_prompt = compose_system_prompt(self.execution_mode)

        user_message = compose_user_message(context.stage, context)
        logger.info(
            "Lead agent dispatch started | mode=%s stage=%s",
            self.execution_mode.value,
            context.stage.value,
        )
        logger.debug(
            "Lead agent resume | resume_session_id=%s send_system=%s system_chars=%s user_chars=%s",
            resume_session_id,
            bool(system_prompt),
            len(system_prompt) if system_prompt else 0,
            len(user_message),
        )

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            disallowed_tools=[
                "Bash",
                "Write",
                "Edit",
                "Read",
                "Glob",
                "Grep",
                "WebSearch",
                "WebFetch",
                "Task",
            ],
            output_format=LEAD_AGENT_OUTPUT_SCHEMA,
            permission_mode="default",
            cwd=self.working_directory,
            resume=resume_session_id,
        )

        async with ClaudeSDKClient(options=options) as client:
            await client.query(user_message)

            while True:
                async for msg in client.receive_response():
                    if AssistantMessage is not None and isinstance(msg, AssistantMessage):
                        logger.debug("Lead SDK message | type=%s", type(msg).__name__)
                        ask = self._extract_ask_user_question(msg)
                        if ask is None:
                            continue
                        if self.execution_mode == ExecutionMode.YOLO:
                            raise ValidationError("AskUserQuestion used in YOLO mode")
                        if self.hitl_callback is None:
                            raise ValidationError(
                                "AskUserQuestion requested but no hitl_callback configured"
                            )
                        logger.info("AskUserQuestion fired | questions=%d", len(ask.questions))
                        await self._answer_questions(client, ask)
                        # Start a fresh receive_response() for the follow-up query.
                        break

                    # Tool results can appear as UserMessage; ignore them and wait for ResultMessage.
                    if UserMessage is not None and isinstance(msg, UserMessage):
                        continue

                    if ResultMessage is not None and isinstance(msg, ResultMessage):
                        self._session_id = getattr(msg, "session_id", None) or self._session_id
                        logger.info("Lead session captured | session_id=%s", self._session_id)
                        output = getattr(msg, "structured_output", None)
                        if output is None:
                            # SDKs may use different attribute names depending on version.
                            output = getattr(msg, "output", None)
                        if output is None:
                            output = getattr(msg, "result", None)
                        return self._parse_structured_output(output)
                else:
                    # receive_response() exhausted without yielding a ResultMessage.
                    raise ParseError("Lead agent did not produce a ResultMessage")

    def _extract_ask_user_question(self, msg) -> _AskUserQuestion | None:
        if not hasattr(msg, "content"):
            return None
        for block in msg.content or []:
            if ToolUseBlock is not None and isinstance(block, ToolUseBlock) and block.name == "AskUserQuestion":
                inp = getattr(block, "input", None) or {}
                questions = inp.get("questions")
                if isinstance(questions, list):
                    return _AskUserQuestion(questions=questions)
        return None

    async def _answer_questions(self, client, ask: _AskUserQuestion) -> None:
        # Convert SDK question payload to our typed questions.
        typed: list[ClarificationQuestion] = []
        for idx, q in enumerate(ask.questions):
            if not isinstance(q, dict):
                continue
            qid = str(q.get("header") or q.get("id") or f"q{idx}")
            qtext = str(q.get("question") or "")
            opts_raw = q.get("options") or []
            opts: list[str] = []
            if isinstance(opts_raw, list):
                for o in opts_raw:
                    if isinstance(o, dict) and "label" in o:
                        opts.append(str(o["label"]))
                    elif isinstance(o, str):
                        opts.append(o)
            typed.append(ClarificationQuestion(id=qid, question=qtext, options=opts or None, required=True))

        answers = await self.hitl_callback(typed)  # type: ignore[misc]

        # Feed answers back via a follow-up query. This matches the documented pattern where
        # AskUserQuestion is handled by the SDK, and the user response is provided as text.
        lines: list[str] = []
        for a in answers:
            lines.append(f"{a.question_id}: {a.answer}")
        payload = "\n".join(lines).strip() or "(no answers)"
        await client.query(payload)

    def _parse_structured_output(self, output: Any) -> ExecutionPlanResponse:
        if output is None:
            raise ParseError("structured_output is not an object", detail="structured_output=None")

        # Some SDK versions return JSON as a string.
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except Exception as e:
                raise ParseError(
                    "structured_output is not an object",
                    detail=f"structured_output is str but not valid JSON: {type(e).__name__}: {e}; value={output[:500]!r}",
                ) from e

        # Some SDK versions return pydantic models / objects with `model_dump()`.
        if not isinstance(output, dict) and hasattr(output, "model_dump"):
            try:
                output = output.model_dump()
            except Exception as e:
                raise ParseError(
                    "structured_output is not an object",
                    detail=f"structured_output has model_dump() but it failed: {type(e).__name__}: {e}",
                ) from e

        if not isinstance(output, dict):
            raise ParseError(
                "structured_output is not an object",
                detail=f"type={type(output).__name__} value={repr(output)[:500]}",
            )

        otype = output.get("type")
        if otype == "terminate":
            term = TerminateResponse(**output)
            logger.error("Lead agent terminated | reason=%s", term.reason)
            raise LeadAgentTerminationError(term.reason)

        if otype == "execution_plan":
            plan = ExecutionPlanResponse(**output)
            errors = validate_lead_response(plan)
            if errors:
                logger.error("Lead plan validation failed | errors=%s", "; ".join(errors))
                raise ValidationError("Invalid execution plan response", detail="; ".join(errors))
            logger.info("Lead agent plan received | tasks=%d", len(plan.tasks))
            return plan

        logger.error("Lead parse error | unknown_type=%s", str(otype))
        raise ParseError("Unknown structured_output type", detail=str(otype))

