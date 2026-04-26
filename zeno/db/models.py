from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Float,
    Integer,
    String,
    Text,
    Uuid,
    PrimaryKeyConstraint,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from zeno.core.enums import ExecutionMode, OrchestratorState

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# --- Enums (stored as VARCHAR in SQLite) ---


def _values(cls: type[enum.Enum]) -> list[str]:
    return [e.value for e in cls]


class SessionStatus(str, enum.Enum):
    active = "active"
    completed = "completed"
    failed = "failed"
    aborted = "aborted"


class PlanStatus(str, enum.Enum):
    draft = "draft"
    approved = "approved"
    active = "active"
    completed = "completed"
    revised = "revised"


class TaskType(str, enum.Enum):
    foundational = "foundational"
    implementation = "implementation"
    validation = "validation"
    integration = "integration"


class TaskStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class AgentType(str, enum.Enum):
    lead = "lead"
    coding = "coding"
    testing = "testing"
    requirements = "requirements"
    integration = "integration"
    other = "other"


class Provider(str, enum.Enum):
    gemini = "gemini"
    anthropic = "anthropic"
    openai = "openai"


class AgentMode(str, enum.Enum):
    adapter = "adapter"
    api = "api"


class AssignmentStatus(str, enum.Enum):
    assigned = "assigned"
    running = "running"
    completed = "completed"
    failed = "failed"


class CheckpointType(str, enum.Enum):
    plan_approval = "plan_approval"
    pre_fanout = "pre_fanout"
    mid_execution = "mid_execution"
    unexpected = "unexpected"


class CheckpointStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    revised = "revised"
    cancelled = "cancelled"


class ArtifactOperation(str, enum.Enum):
    created = "created"
    updated = "updated"
    deleted = "deleted"


# --- ORM models (Db* prefix) ---


class DbSession(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, values_callable=_values, native_enum=False, length=32),
        nullable=False,
        default=SessionStatus.active,
    )
    mode: Mapped[ExecutionMode] = mapped_column(
        Enum(ExecutionMode, values_callable=_values, native_enum=False, length=16),
        nullable=False,
    )
    orchestrator_state: Mapped[OrchestratorState] = mapped_column(
        Enum(OrchestratorState, native_enum=False, length=32),
        nullable=False,
        default=OrchestratorState.INITIALIZING,
    )
    working_directory: Mapped[str] = mapped_column(Text, nullable=False)
    raw_input: Mapped[str] = mapped_column(Text, nullable=False)
    lead_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    execution_plans: Mapped[list[DbExecutionPlan]] = relationship(back_populates="session")
    tasks: Mapped[list[DbTask]] = relationship(
        "DbTask", back_populates="session", foreign_keys="DbTask.session_id"
    )


class DbExecutionPlan(Base):
    __tablename__ = "execution_plans"
    __table_args__ = (UniqueConstraint("session_id", "revision", name="uq_session_revision"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    status: Mapped[PlanStatus] = mapped_column(
        Enum(PlanStatus, values_callable=_values, native_enum=False, length=32),
        nullable=False,
        default=PlanStatus.draft,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    session: Mapped[DbSession] = relationship(back_populates="execution_plans")
    tasks: Mapped[list[DbTask]] = relationship(back_populates="plan")


class DbVault(Base):
    __tablename__ = "vaults"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class DbRoom(Base):
    __tablename__ = "rooms"
    __table_args__ = (UniqueConstraint("vault_id", "name", name="uq_vault_room_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vault_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("vaults.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class DbTask(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("execution_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[TaskType] = mapped_column(
        "type",
        Enum(TaskType, values_callable=_values, native_enum=False, length=32),
        nullable=False,
    )
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, values_callable=_values, native_enum=False, length=32),
        nullable=False,
        default=TaskStatus.pending,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    parallel_group: Mapped[str | None] = mapped_column(String(255), nullable=True)
    worktree_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    branch_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    checkpoint_before: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    plan: Mapped[DbExecutionPlan] = relationship(back_populates="tasks")
    session: Mapped[DbSession] = relationship(foreign_keys=[session_id], back_populates="tasks")


class DbTaskDependency(Base):
    __tablename__ = "task_dependencies"
    __table_args__ = (PrimaryKeyConstraint("task_id", "depends_on_task_id"),)

    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    depends_on_task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )


class DbAgent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    type: Mapped[AgentType] = mapped_column(
        "type",
        Enum(AgentType, values_callable=_values, native_enum=False, length=32),
        nullable=False,
    )
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)

    assignments: Mapped[list["DbAgentAssignment"]] = relationship(back_populates="agent")


class DbAgentAssignment(Base):
    __tablename__ = "agent_assignments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[AssignmentStatus] = mapped_column(
        Enum(AssignmentStatus, values_callable=_values, native_enum=False, length=32),
        nullable=False,
        default=AssignmentStatus.assigned,
    )

    agent: Mapped["DbAgent"] = relationship(back_populates="assignments")


class DbTaskMetrics(Base):
    __tablename__ = "task_metrics"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_assignments.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time_to_first_token_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_input: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_output: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_creation_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    num_turns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    artifacts_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    artifacts_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    artifacts_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DbCheckpoint(Base):
    __tablename__ = "checkpoints"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    type: Mapped[CheckpointType] = mapped_column(
        Enum(CheckpointType, values_callable=_values, native_enum=False, length=32), nullable=False
    )
    status: Mapped[CheckpointStatus] = mapped_column(
        Enum(CheckpointStatus, values_callable=_values, native_enum=False, length=32),
        nullable=False,
        default=CheckpointStatus.pending,
    )
    presented: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class DbArtifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_assignments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    operation: Mapped[ArtifactOperation] = mapped_column(
        Enum(ArtifactOperation, values_callable=_values, native_enum=False, length=32),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
