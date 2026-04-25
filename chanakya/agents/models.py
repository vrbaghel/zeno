from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from chanakya.core.enums import OrchestratorState
from chanakya.memory.models import MemDiaryEntry


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


AdaptorRole = Literal["system", "user", "assistant", "tool"]


class AdaptorMessage(BaseModel):
    role: AdaptorRole
    content: str


class AdaptorArtifacts(BaseModel):
    created: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    deleted: list[str] = Field(default_factory=list)


class AdaptorRequestPayload(BaseModel):
    system: str | None = None
    messages: list[AdaptorMessage] = Field(default_factory=list)
    tools: list[dict] = Field(default_factory=list)


class AdaptorRequest(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    session_id: UUID = Field(default_factory=uuid4)
    agent_id: str
    created_at: datetime = Field(default_factory=utc_now)
    payload: AdaptorRequestPayload = Field(default_factory=AdaptorRequestPayload)
    timeout_seconds: float | None = 120.0


class AdaptorResponseStatus(str, Enum):
    success = "success"
    error = "error"
    timeout = "timeout"
    truncated = "truncated"


class AdaptorResponsePayload(BaseModel):
    messages: list[AdaptorMessage] = Field(default_factory=list)


class DiaryEntry(BaseModel):
    summary: str
    decisions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    open_issues: list[str] = Field(default_factory=list)
    room: str


class AgentResponse(BaseModel):
    status: Literal["success", "error", "truncated"]
    payload: AdaptorResponsePayload = Field(default_factory=AdaptorResponsePayload)
    artifacts: AdaptorArtifacts = Field(default_factory=AdaptorArtifacts)
    diary_entry: DiaryEntry | None = None


class AdaptorResponse(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    request_id: UUID
    session_id: UUID
    agent_id: str
    status: AdaptorResponseStatus
    created_at: datetime = Field(default_factory=utc_now)
    payload: AdaptorResponsePayload = Field(default_factory=AdaptorResponsePayload)
    artifacts: AdaptorArtifacts = Field(default_factory=AdaptorArtifacts)


class AdaptorErrorCode(str, Enum):
    ADAPTOR_NOT_FOUND = "ADAPTOR_NOT_FOUND"
    ADAPTOR_SPAWN_FAILED = "ADAPTOR_SPAWN_FAILED"
    ADAPTOR_TIMEOUT = "ADAPTOR_TIMEOUT"
    ADAPTOR_PARSE_ERROR = "ADAPTOR_PARSE_ERROR"
    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"
    RESPONSE_TRUNCATED = "RESPONSE_TRUNCATED"
    UNKNOWN = "UNKNOWN"


class AdaptorError(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    request_id: UUID | None = None
    agent_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    code: AdaptorErrorCode
    message: str
    recoverable: bool = False


class AdaptorTimingMetrics(BaseModel):
    queued_at: datetime | None = None
    dispatched_at: datetime | None = None
    first_token_at: datetime | None = None
    completed_at: datetime | None = None
    latency_ms: int | None = None
    time_to_first_token_ms: int | None = None


class AdaptorTokenMetrics(BaseModel):
    input: int | None = None
    output: int | None = None
    total: int | None = None
    deviation: str | None = None


class AdaptorArtifactMetrics(BaseModel):
    created_count: int = 0
    updated_count: int = 0
    deleted_count: int = 0


class AdaptorMetrics(BaseModel):
    timing: AdaptorTimingMetrics = Field(default_factory=AdaptorTimingMetrics)
    tokens: AdaptorTokenMetrics = Field(default_factory=AdaptorTokenMetrics)
    artifacts: AdaptorArtifactMetrics = Field(default_factory=AdaptorArtifactMetrics)
    agent_id: str
    mode: Literal["adapter", "api"]
    provider: Literal["gemini", "anthropic", "openai"]
    model: str | None = None


# ---------------------------------------------------------------------------
# Phase 5: Lead agent response/request contracts
# ---------------------------------------------------------------------------


class ClarificationQuestion(BaseModel):
    id: str
    question: str
    options: list[str] | None = None
    required: bool = True


class ClarificationResponse(BaseModel):
    type: Literal["clarification"] = "clarification"
    questions: list[ClarificationQuestion] = Field(default_factory=list)
    context: str


class RoomDefinition(BaseModel):
    name: str
    description: str


TaskTypeLiteral = Literal["foundational", "implementation", "validation", "integration"]
LeadAgentTypeLiteral = Literal["requirements", "coding", "testing", "merge", "lead"]


class TaskDefinition(BaseModel):
    id: str
    title: str
    description: str
    type: TaskTypeLiteral
    agent_type: LeadAgentTypeLiteral
    provider: str
    room: str
    depends_on: list[str] = Field(default_factory=list)
    parallel_group: str | None = None
    checkpoint_before: bool = False


class ExecutionPlanResponse(BaseModel):
    type: Literal["execution_plan"] = "execution_plan"
    task_summary: str
    rooms: list[RoomDefinition] = Field(default_factory=list)
    tasks: list[TaskDefinition] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    diary_entry: DiaryEntry


# ---------------------------------------------------------------------------
# Phase 8A: Unified lead agent response schema
# ---------------------------------------------------------------------------


class OptionDefinition(BaseModel):
    label: str
    description: str


class UserOptions(BaseModel):
    option_a: OptionDefinition
    option_b: OptionDefinition
    option_c: OptionDefinition


class LeadAgentResponse(BaseModel):
    type: Literal["execution", "clarification", "terminate"]

    # clarification
    question: str | None = None
    context: str | None = None
    options: UserOptions | None = None

    # execution
    task_summary: str | None = None
    rooms: list[RoomDefinition] | None = None
    tasks: list[TaskDefinition] | None = None
    assumptions: list[str] | None = None
    diary_entry: MemDiaryEntry | None = None

    # terminate
    reason: str | None = None


class ClarificationInput(BaseModel):
    type: Literal["clarification_response"] = "clarification_response"
    question: str
    choice: Literal["a", "b", "c"]
    label: str


class ClarificationAnswer(BaseModel):
    question_id: str
    answer: str


class AgentContext(BaseModel):
    session_summary: str
    relevant_prior_work: list[str] = Field(default_factory=list)
    agent_history: list[str] = Field(default_factory=list)


LeadRequestType = Literal[
    "initial",
    "clarification_response",
    "revision",
    "checkpoint_consultation",
]


class LeadAgentRequest(BaseModel):
    type: LeadRequestType
    session_id: UUID
    raw_input: str
    mode: Literal["yolo", "hitl"]
    memory_context: AgentContext
    payload: dict[str, Any] = Field(default_factory=dict)


class CheckpointOption(BaseModel):
    key: Literal["approve", "revise", "cancel", "retry", "skip", "a", "b", "c"]
    label: str


CheckpointContentType = Literal["plan_approval", "pre_fanout", "unexpected"]


class CheckpointContent(BaseModel):
    type: CheckpointContentType
    title: str
    description: str
    options: list[CheckpointOption] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


def validate_lead_response(
    response: LeadAgentResponse,
    available_providers: list[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    rtype = response.type

    if rtype not in {"execution", "clarification", "terminate"}:
        errors.append('type must be exactly "execution" | "clarification" | "terminate"')
        return errors

    if rtype == "clarification":
        if not (response.question and response.question.strip()):
            errors.append("question must be non-empty for type=clarification")

        if response.options is None:
            errors.append("options must be present for type=clarification")
            return errors

        # Ensure the three options exist and labels are non-empty.
        for key, opt in (
            ("option_a", response.options.option_a),
            ("option_b", response.options.option_b),
            ("option_c", response.options.option_c),
        ):
            if opt is None:
                errors.append(f"{key} must be non-null for type=clarification")
                continue
            if not (opt.label and opt.label.strip()):
                errors.append(f"{key}.label must be a non-empty string for type=clarification")

        return errors

    if rtype == "terminate":
        if not (response.reason and response.reason.strip()):
            errors.append("reason must be non-empty string for type=terminate")
        return errors

    # execution rules
    if not response.tasks:
        errors.append("tasks must be non-empty list for type=execution")
        return errors
    if not response.rooms:
        errors.append("rooms must be non-empty list for type=execution")
        return errors
    if response.diary_entry is None:
        errors.append("diary_entry must be present for type=execution")

    room_names = {r.name for r in response.rooms}
    task_ids = {t.id for t in response.tasks}

    for t in response.tasks:
        if not (t.provider and t.provider.strip()):
            errors.append(f"task {t.id}: provider must be present for type=execution")
        elif available_providers is not None and t.provider not in set(available_providers):
            errors.append(
                f"task {t.id}: provider {t.provider!r} is not in available_providers"
            )

        if t.parallel_group is not None:
            pg = t.parallel_group
            if not (len(pg) == 1 and "A" <= pg <= "Z"):
                errors.append(
                    f"task {t.id}: parallel_group must be null or a single uppercase letter A-Z"
                )

        for dep in t.depends_on:
            if dep not in task_ids:
                errors.append(f"task {t.id}: depends_on references unknown task id {dep!r}")

        if t.room not in room_names:
            errors.append(f"task {t.id}: room {t.room!r} not found in rooms list")

    return errors

