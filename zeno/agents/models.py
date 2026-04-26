from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from zeno.memory.models import MemLog


AgentRole = Literal["system", "user", "assistant", "tool"]


class AgentMessage(BaseModel):
    role: AgentRole
    content: str


class AgentArtifacts(BaseModel):
    created: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    deleted: list[str] = Field(default_factory=list)


class AgentResponse(BaseModel):
    """
    Minimal response contract Zeno needs back from an agent under the SDK model:
    - summary: what was done
    - artifacts: files created/updated/deleted
    - log: MemLog diary entry for ChromaDB
    """

    summary: str
    artifacts: AgentArtifacts = Field(default_factory=AgentArtifacts)
    log: MemLog


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
    agent_responsibilities: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    parallel_group: str | None = None
    checkpoint_before: bool = False


class ExecutionPlanResponse(BaseModel):
    type: Literal["execution_plan"] = "execution_plan"
    task_summary: str
    rooms: list[RoomDefinition] = Field(default_factory=list)
    tasks: list[TaskDefinition] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    log: MemLog


class LeadAgentResponse(BaseModel):
    type: Literal["execution", "clarification", "terminate"]

    # clarification
    question: str | None = None
    context: str | None = None
    options: list[str] | None = None

    # execution
    task_summary: str | None = None
    rooms: list[RoomDefinition] | None = None
    tasks: list[TaskDefinition] | None = None
    assumptions: list[str] | None = None
    log: MemLog | None = None

    # terminate
    reason: str | None = None


class ClarificationInput(BaseModel):
    type: Literal["clarification_response"] = "clarification_response"
    question: str
    choice: str
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
    if response.log is None:
        errors.append("log must be present for type=execution")

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

