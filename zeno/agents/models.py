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


class WorkerResponse(BaseModel):
    summary: str
    artifacts: AgentArtifacts = Field(default_factory=AgentArtifacts)
    log: MemLog


class TerminateResponse(BaseModel):
    type: Literal["terminate"] = "terminate"
    reason: str


# ---------------------------------------------------------------------------
# Phase 5: Lead agent response/request contracts
# ---------------------------------------------------------------------------


class ClarificationQuestion(BaseModel):
    id: str
    question: str
    options: list[str] | None = None
    required: bool = True


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
    response: ExecutionPlanResponse,
) -> list[str]:
    errors: list[str] = []
    if response.type != "execution_plan":
        errors.append('type must be exactly "execution_plan"')
        return errors

    if not response.tasks:
        errors.append("tasks must be non-empty list for type=execution_plan")
        return errors
    if not response.rooms:
        errors.append("rooms must be non-empty list for type=execution_plan")
        return errors

    room_names = {r.name for r in response.rooms}
    task_ids = {t.id for t in response.tasks}

    for t in response.tasks:
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

