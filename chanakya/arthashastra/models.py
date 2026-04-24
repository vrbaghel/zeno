from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from chanakya.core.enums import OrchestratorState


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
    timeout_seconds: float | None = 60.0


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
    key: Literal["approve", "revise", "cancel", "retry", "skip"]
    label: str


CheckpointContentType = Literal["plan_approval", "pre_fanout", "unexpected"]


class CheckpointContent(BaseModel):
    type: CheckpointContentType
    title: str
    description: str
    options: list[CheckpointOption] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


def validate_lead_response(
    lead_response: dict[str, Any] | ClarificationResponse | ExecutionPlanResponse,
    *,
    mode: Literal["yolo", "hitl"],
    is_revision: bool = False,
    completed_tasks: set[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    completed_tasks = completed_tasks or set()

    if isinstance(lead_response, dict):
        rtype = lead_response.get("type")
    else:
        rtype = lead_response.type

    if rtype not in {"clarification", "execution_plan"}:
        errors.append('type must be exactly "clarification" or "execution_plan"')
        return errors

    if mode == "yolo" and rtype == "clarification":
        errors.append("YOLO mode does not allow clarification responses")
        return errors

    if rtype == "clarification":
        # Nothing else from the 8 rules applies besides discriminator + YOLO reject.
        return errors

    # execution_plan rules
    try:
        plan = lead_response if isinstance(lead_response, ExecutionPlanResponse) else ExecutionPlanResponse.model_validate(lead_response)
    except Exception as e:
        return [f"execution_plan parse error: {e}"]

    if plan.diary_entry is None:
        errors.append("diary_entry must be present in ExecutionPlanResponse")

    if not plan.tasks:
        errors.append("ExecutionPlanResponse.tasks must contain at least one task")
        return errors

    room_names = {r.name for r in plan.rooms}
    task_ids = {t.id for t in plan.tasks}

    for t in plan.tasks:
        if t.parallel_group is not None:
            pg = t.parallel_group
            if not (len(pg) == 1 and "A" <= pg <= "Z"):
                errors.append(f"task {t.id}: parallel_group must be null or a single uppercase letter A-Z")

        for dep in t.depends_on:
            if dep not in task_ids:
                errors.append(f"task {t.id}: depends_on references unknown task id {dep!r}")

        if t.room not in room_names:
            errors.append(f"task {t.id}: room {t.room!r} not found in rooms list")

        if is_revision and completed_tasks:
            for dep in t.depends_on:
                if dep in completed_tasks:
                    errors.append(
                        f"revision invalid: task {t.id} depends_on includes completed task id {dep!r}"
                    )

    return errors

