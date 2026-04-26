from __future__ import annotations

from datetime import datetime
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
    type: Literal["success"] = "success"
    summary: str
    artifacts: AgentArtifacts = Field(default_factory=AgentArtifacts)
    log: MemLog


class WorkerTerminateResponse(BaseModel):
    type: Literal["terminate"] = "terminate"
    reason: str


class WorkerMetrics(BaseModel):
    queued_at: datetime
    first_token_at: datetime | None = None
    completed_at: datetime

    latency_ms: int
    time_to_first_token_ms: int | None = None

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None

    cost_usd: float | None = None
    model: str | None = None
    num_turns: int | None = None


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


class TaskStatusEntry(BaseModel):
    """Snapshot of a task for lead continuation / revision context."""

    title: str
    status: Literal["completed", "running", "pending", "failed"]
    summary: str | None = None


class TaskDefinition(BaseModel):
    id: str
    title: str
    description: str
    type: str
    agent_type: str
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
    is_final: bool = True


WORKER_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["success", "terminate"]}
        },
        "required": ["type"],
        "if": {
            "properties": {"type": {"const": "success"}},
        },
        "then": {
            "type": "object",
            "properties": {
                "type": {"type": "string"},
                "summary": {"type": "string"},
                "artifacts": {
                    "type": "object",
                    "properties": {
                        "created": {"type": "array", "items": {"type": "string"}},
                        "updated": {"type": "array", "items": {"type": "string"}},
                        "deleted": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["created", "updated", "deleted"],
                },
                "log": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "decisions": {"type": "array", "items": {"type": "string"}},
                        "assumptions": {"type": "array", "items": {"type": "string"}},
                        "dependencies": {"type": "array", "items": {"type": "string"}},
                        "open_issues": {"type": "array", "items": {"type": "string"}},
                        "room": {"type": "string"},
                    },
                    "required": ["summary", "decisions", "assumptions", "open_issues", "room"]
                }
            },
            "required": ["type", "summary", "artifacts", "log"],
        },
        "else": {
            "type": "object",
            "properties": {
                "type": {"type": "string"},
                "reason": {"type": "string"}
            },
            "required": ["type", "reason"],
        }
    }
}


LEAD_AGENT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["execution_plan", "terminate"]
            }
        },
        "required": ["type"],
        "if": {
            "properties": {"type": {"const": "execution_plan"}}
        },
        "then": {
            "properties": {
                "type": {"type": "string"},
                "task_summary": {"type": "string"},
                "rooms": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"}
                        },
                        "required": ["name", "description"]
                    }
                },
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "type": {"type": "string"},
                            "agent_type": {"type": "string"},
                            "agent_responsibilities": {"type": "string"},
                            "room": {"type": "string"},
                            "depends_on": {"type": "array", "items": {"type": "string"}},
                            "parallel_group": {"type": ["string", "null"]},
                            "checkpoint_before": {"type": "boolean"}
                        },
                        "required": ["id", "title", "description", "type",
                                     "agent_type", "agent_responsibilities",
                                     "room", "depends_on", "parallel_group",
                                     "checkpoint_before"]
                    }
                },
                "assumptions": {"type": "array", "items": {"type": "string"}},
                "is_final": {"type": "boolean"},
                "log": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "decisions": {"type": "array", "items": {"type": "string"}},
                        "assumptions": {"type": "array", "items": {"type": "string"}},
                        "dependencies": {"type": "array", "items": {"type": "string"}},
                        "open_issues": {"type": "array", "items": {"type": "string"}},
                        "room": {"type": "string"},
                    },
                    "required": ["summary", "decisions", "assumptions", "open_issues", "room"]
                }
            },
            "required": ["type", "task_summary", "rooms", "tasks", "assumptions", "is_final", "log"]
        },
        "else": {
            "properties": {
                "type": {"type": "string"},
                "reason": {"type": "string"}
            },
            "required": ["type", "reason"]
        }
    }
}


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
    relevant_traces: list[str] = Field(default_factory=list)
    agent_logs: list[str] = Field(default_factory=list)


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
    key: Literal["approve", "revise", "cancel", "retry", "skip", "a", "b", "c", "d"]
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

    if not response.is_final and not any(t.checkpoint_before for t in response.tasks):
        errors.append(
            "non-final chunk must contain at least one task with checkpoint_before: true"
        )

    return errors

