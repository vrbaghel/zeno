from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from zeno.agents.lead.composer import compose_prompt
from zeno.agents.models import (
    AgentContext,
    ClarificationAnswer,
    ClarificationQuestion,
    ExecutionPlanResponse,
    TerminateResponse,
    validate_lead_response,
)
from zeno.core.enums import ExecutionMode, LeadAgentStage
from zeno.orchestrator.errors import LeadAgentTerminationError, ParseError, ValidationError

try:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
    from claude_agent_sdk.messages import AssistantMessage, ResultMessage, UserMessage
    from claude_agent_sdk.content import ToolResultBlock, ToolUseBlock
except Exception:  # pragma: no cover
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
        return await self._run(context=context, resume_session_id=None)

    async def revise(self, context: LeadAgentContext) -> ExecutionPlanResponse:
        if not self._session_id:
            raise ValidationError("Cannot revise before dispatch()", detail="session_id is not set")
        return await self._run(context=context, resume_session_id=self._session_id)

    def _output_schema(self) -> dict[str, Any]:
        # Union schema discriminated by `type`.
        exec_schema = ExecutionPlanResponse.model_json_schema()
        term_schema = TerminateResponse.model_json_schema()
        return {
            "type": "json_schema",
            "schema": {
                "oneOf": [exec_schema, term_schema],
                "discriminator": {"propertyName": "type"},
            },
        }

    async def _run(self, *, context: LeadAgentContext, resume_session_id: str | None) -> ExecutionPlanResponse:
        if ClaudeSDKClient is None or ClaudeAgentOptions is None:
            raise ValidationError(
                "claude-agent-sdk is not available in this environment",
                detail="Install claude-agent-sdk and ensure it is importable.",
            )

        system_prompt = compose_prompt(
            mode=self.execution_mode,
            stage=context.stage,
            context=context,
        )

        allowed_tools: list[str] = []
        if self.execution_mode == ExecutionMode.HITL:
            allowed_tools = ["AskUserQuestion"]

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
            permission_mode="acceptEdits",
            output_format=self._output_schema(),
            cwd=self.working_directory,
            resume=resume_session_id,
        )

        async with ClaudeSDKClient(options) as client:
            await client.query(context.raw_input)

            async for msg in client.receive_response():
                if AssistantMessage is not None and isinstance(msg, AssistantMessage):
                    ask = self._extract_ask_user_question(msg)
                    if ask is None:
                        continue
                    if self.execution_mode == ExecutionMode.YOLO:
                        raise ValidationError("AskUserQuestion used in YOLO mode")
                    if self.hitl_callback is None:
                        raise ValidationError("AskUserQuestion requested but no hitl_callback configured")
                    await self._answer_questions(client, ask)
                    continue

                # Tool results can appear as UserMessage; ignore them and wait for ResultMessage.
                if UserMessage is not None and isinstance(msg, UserMessage):
                    continue

                if ResultMessage is not None and isinstance(msg, ResultMessage):
                    self._session_id = getattr(msg, "session_id", None) or self._session_id
                    output = getattr(msg, "structured_output", None)
                    return self._parse_structured_output(output)

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
        if not isinstance(output, dict):
            raise ParseError("structured_output is not an object", detail=repr(output)[:500])

        otype = output.get("type")
        if otype == "terminate":
            term = TerminateResponse(**output)
            raise LeadAgentTerminationError(term.reason)

        if otype == "execution_plan":
            plan = ExecutionPlanResponse(**output)
            errors = validate_lead_response(plan)
            if errors:
                raise ValidationError("Invalid execution plan response", detail="; ".join(errors))
            return plan

        raise ParseError("Unknown structured_output type", detail=str(otype))

