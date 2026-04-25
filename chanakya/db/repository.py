from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from chanakya.agents.models import AdaptorArtifacts, AdaptorMetrics
from chanakya.db.engine import get_session_factory
from chanakya.db.models import (
    AgentMode,
    AgentType,
    ArtifactOperation,
    AssignmentStatus,
    CheckpointStatus,
    CheckpointType,
    DbAgent,
    DbAgentAssignment,
    DbArtifact,
    DbCheckpoint,
    DbExecutionPlan,
    DbSession,
    DbTask,
    DbTaskDependency,
    DbTaskMetrics,
    DbRoom,
    DbWing,
    PlanStatus,
    Provider,
    SessionStatus,
    TaskStatus,
    TaskType,
)
from chanakya.core.enums import ExecutionMode, OrchestratorState

# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _next_plan_revision(db: AsyncSession, session_id: uuid.UUID) -> int:
    res = await db.execute(
        select(func.max(DbExecutionPlan.revision)).where(DbExecutionPlan.session_id == session_id)
    )
    m = res.scalar_one()
    return (m or 0) + 1


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


async def create_session(
    mode: ExecutionMode, working_directory: str, raw_input: str, *, id: uuid.UUID | None = None
) -> DbSession:
    factory = get_session_factory()
    async with factory() as db:
        s = DbSession(
            id=id or uuid.uuid4(),
            mode=mode,
            working_directory=working_directory,
            raw_input=raw_input,
            status=SessionStatus.active,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(s)
        await db.commit()
        await db.refresh(s)
        return s


async def get_session(session_id: uuid.UUID) -> DbSession | None:
    factory = get_session_factory()
    async with factory() as db:
        r = await db.get(DbSession, session_id)
        return r


async def update_session_status(session_id: uuid.UUID, status: SessionStatus) -> None:
    factory = get_session_factory()
    async with factory() as db:
        s = await db.get(DbSession, session_id)
        if s is None:
            raise KeyError("session not found")
        s.status = status
        s.updated_at = _now()
        await db.commit()


async def update_orchestrator_state(session_id: uuid.UUID, state: OrchestratorState) -> None:
    factory = get_session_factory()
    async with factory() as db:
        s = await db.get(DbSession, session_id)
        if s is None:
            raise KeyError("session not found")
        s.orchestrator_state = state
        s.updated_at = _now()
        await db.commit()


async def get_orchestrator_state(session_id: uuid.UUID) -> OrchestratorState:
    factory = get_session_factory()
    async with factory() as db:
        s = await db.get(DbSession, session_id)
        if s is None:
            raise KeyError("session not found")
        return s.orchestrator_state


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------


async def create_execution_plan(session_id: uuid.UUID) -> DbExecutionPlan:
    factory = get_session_factory()
    async with factory() as db:
        s = await db.get(DbSession, session_id)
        if s is None:
            raise KeyError("session not found")
        rev = await _next_plan_revision(db, session_id)
        p = DbExecutionPlan(
            session_id=session_id,
            revision=rev,
            status=PlanStatus.draft,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(p)
        await db.commit()
        await db.refresh(p)
        return p


async def get_active_plan(session_id: uuid.UUID) -> DbExecutionPlan | None:
    factory = get_session_factory()
    async with factory() as db:
        res = await db.execute(
            select(DbExecutionPlan)
            .where(DbExecutionPlan.session_id == session_id)
            .order_by(DbExecutionPlan.revision.desc())
            .limit(1)
        )
        return res.scalar_one_or_none()


async def revise_plan(plan_id: uuid.UUID) -> DbExecutionPlan:
    factory = get_session_factory()
    async with factory() as db:
        old = await db.get(DbExecutionPlan, plan_id)
        if old is None:
            raise KeyError("plan not found")
        old.status = PlanStatus.revised
        old.updated_at = _now()
        nrev = await _next_plan_revision(db, old.session_id)
        new = DbExecutionPlan(
            session_id=old.session_id,
            revision=nrev,
            status=PlanStatus.draft,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(new)
        await db.commit()
        await db.refresh(new)
        return new


# ---------------------------------------------------------------------------
# Wings + rooms
# ---------------------------------------------------------------------------


async def create_wing(name: str, path: str, *, id: uuid.UUID | None = None) -> DbWing:
    factory = get_session_factory()
    async with factory() as db:
        w = DbWing(id=id or uuid.uuid4(), name=name, path=path, created_at=_now())
        db.add(w)
        await db.commit()
        await db.refresh(w)
        return w


async def get_wing_by_path(path: str) -> DbWing | None:
    factory = get_session_factory()
    async with factory() as db:
        r = await db.execute(select(DbWing).where(DbWing.path == path).limit(1))
        return r.scalar_one_or_none()


async def get_wing(wing_id: uuid.UUID) -> DbWing | None:
    factory = get_session_factory()
    async with factory() as db:
        return await db.get(DbWing, wing_id)


async def create_room(
    wing_id: uuid.UUID, name: str, description: str, *, id: uuid.UUID | None = None
) -> DbRoom:
    factory = get_session_factory()
    async with factory() as db:
        r = DbRoom(
            id=id or uuid.uuid4(),
            wing_id=wing_id,
            name=name,
            description=description,
            created_at=_now(),
        )
        db.add(r)
        await db.commit()
        await db.refresh(r)
        return r


async def get_rooms(wing_id: uuid.UUID) -> list[DbRoom]:
    factory = get_session_factory()
    async with factory() as db:
        r = await db.execute(
            select(DbRoom).where(DbRoom.wing_id == wing_id).order_by(DbRoom.created_at.asc())
        )
        return list(r.scalars().all())


async def get_room_by_name(wing_id: uuid.UUID, name: str) -> DbRoom | None:
    factory = get_session_factory()
    async with factory() as db:
        r = await db.execute(
            select(DbRoom).where(DbRoom.wing_id == wing_id, DbRoom.name == name).limit(1)
        )
        return r.scalar_one_or_none()


async def room_exists(wing_id: uuid.UUID, name: str) -> bool:
    return (await get_room_by_name(wing_id, name)) is not None


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


async def create_task(
    plan_id: uuid.UUID,
    session_id: uuid.UUID,
    title: str,
    description: str,
    task_type: TaskType,
    *,
    priority: int = 100,
    parallel_group: str | None = None,
    checkpoint_before: bool = False,
) -> DbTask:
    factory = get_session_factory()
    async with factory() as db:
        t = DbTask(
            plan_id=plan_id,
            session_id=session_id,
            title=title,
            description=description,
            type=task_type,
            status=TaskStatus.pending,
            priority=priority,
            parallel_group=parallel_group,
            checkpoint_before=checkpoint_before,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(t)
        await db.commit()
        await db.refresh(t)
        return t


async def add_task_dependency(task_id: uuid.UUID, depends_on_task_id: uuid.UUID) -> None:
    if task_id == depends_on_task_id:
        raise ValueError("self-dependency not allowed")
    factory = get_session_factory()
    async with factory() as db:
        d = DbTaskDependency(task_id=task_id, depends_on_task_id=depends_on_task_id)
        db.add(d)
        await db.commit()


async def get_tasks_by_plan(plan_id: uuid.UUID) -> list[DbTask]:
    factory = get_session_factory()
    async with factory() as db:
        r = await db.execute(
            select(DbTask)
            .where(DbTask.plan_id == plan_id)
            .order_by(DbTask.priority.asc(), DbTask.created_at.asc())
        )
        return list(r.scalars().all())


async def get_pending_tasks(plan_id: uuid.UUID) -> list[DbTask]:
    factory = get_session_factory()
    async with factory() as db:
        r = await db.execute(
            select(DbTask)
            .where(DbTask.plan_id == plan_id, DbTask.status == TaskStatus.pending)
            .order_by(DbTask.priority.asc(), DbTask.created_at.asc())
        )
        return list(r.scalars().all())


async def get_runnable_tasks(plan_id: uuid.UUID) -> list[DbTask]:
    pending = await get_pending_tasks(plan_id)
    factory = get_session_factory()
    async with factory() as db:
        out: list[DbTask] = []
        for t in pending:
            r = await db.execute(
                select(DbTaskDependency.depends_on_task_id).where(DbTaskDependency.task_id == t.id)
            )
            dep_ids = [row[0] for row in r.all()]
            if not dep_ids:
                out.append(t)
                continue
            r2 = await db.execute(select(DbTask.id, DbTask.status).where(DbTask.id.in_(dep_ids)))
            status_by: dict[uuid.UUID, TaskStatus] = {row[0]: row[1] for row in r2.all()}
            if all(status_by.get(d) == TaskStatus.completed for d in dep_ids):
                out.append(t)
        out.sort(key=lambda x: (x.priority, x.created_at))
        return out


async def update_task_status(task_id: uuid.UUID, status: TaskStatus) -> None:
    factory = get_session_factory()
    async with factory() as db:
        t = await db.get(DbTask, task_id)
        if t is None:
            raise KeyError("task not found")
        t.status = status
        t.updated_at = _now()
        await db.commit()


async def complete_task(task_id: uuid.UUID, result_summary: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        t = await db.get(DbTask, task_id)
        if t is None:
            raise KeyError("task not found")
        t.status = TaskStatus.completed
        t.result_summary = result_summary
        t.updated_at = _now()
        await db.commit()


async def assign_worktree(task_id: uuid.UUID, worktree_path: str, branch_name: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        t = await db.get(DbTask, task_id)
        if t is None:
            raise KeyError("task not found")
        t.worktree_path = worktree_path
        t.branch_name = branch_name
        t.updated_at = _now()
        await db.commit()


async def clear_worktree(task_id: uuid.UUID) -> None:
    factory = get_session_factory()
    async with factory() as db:
        t = await db.get(DbTask, task_id)
        if t is None:
            raise KeyError("task not found")
        t.worktree_path = None
        t.branch_name = None
        t.updated_at = _now()
        await db.commit()


async def get_tasks_with_worktrees(session_id: uuid.UUID) -> list[DbTask]:
    factory = get_session_factory()
    async with factory() as db:
        r = await db.execute(
            select(DbTask)
            .where(DbTask.session_id == session_id, DbTask.worktree_path.is_not(None))
            .order_by(DbTask.created_at.asc())
        )
        return list(r.scalars().all())


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


async def create_agent(
    name: str,
    agent_type: AgentType,
    system_prompt: str,
    provider: Provider,
    mode: AgentMode,  # noqa: A002
    *,
    agent_id: uuid.UUID | None = None,
) -> DbAgent:
    factory = get_session_factory()
    async with factory() as db:
        a = DbAgent(
            id=agent_id or uuid.uuid4(),
            name=name,
            type=agent_type,
            system_prompt=system_prompt,
            provider=provider,
            mode=mode,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(a)
        await db.commit()
        await db.refresh(a)
        return a


async def get_agent(agent_id: uuid.UUID) -> DbAgent:
    factory = get_session_factory()
    async with factory() as db:
        a = await db.get(DbAgent, agent_id)
        if a is None:
            raise KeyError("agent not found")
        return a


async def get_agent_by_name(name: str) -> DbAgent | None:
    factory = get_session_factory()
    async with factory() as db:
        r = await db.execute(select(DbAgent).where(DbAgent.name == name).limit(1))
        return r.scalar_one_or_none()


async def get_agent_with_assignments(agent_id: uuid.UUID) -> DbAgent:
    factory = get_session_factory()
    async with factory() as db:
        r = await db.execute(
            select(DbAgent)
            .where(DbAgent.id == agent_id)
            .options(selectinload(DbAgent.assignments))
        )
        a = r.scalar_one_or_none()
        if a is None:
            raise KeyError("agent not found")
        return a


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------


async def create_assignment(
    task_id: uuid.UUID,
    session_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> DbAgentAssignment:
    factory = get_session_factory()
    async with factory() as db:
        ag = await db.get(DbAgent, agent_id)
        if ag is None:
            raise KeyError("agent not found")
        a = DbAgentAssignment(
            task_id=task_id,
            session_id=session_id,
            agent_id=agent_id,
            status=AssignmentStatus.assigned,
            created_at=_now(),
        )
        db.add(a)
        await db.commit()
        await db.refresh(a)
        return a


async def start_assignment(assignment_id: uuid.UUID) -> None:
    factory = get_session_factory()
    async with factory() as db:
        a = await db.get(DbAgentAssignment, assignment_id)
        if a is None:
            raise KeyError("assignment not found")
        a.started_at = _now()
        a.status = AssignmentStatus.running
        await db.commit()


async def complete_assignment(assignment_id: uuid.UUID) -> None:
    factory = get_session_factory()
    async with factory() as db:
        a = await db.get(DbAgentAssignment, assignment_id)
        if a is None:
            raise KeyError("assignment not found")
        a.completed_at = _now()
        a.status = AssignmentStatus.completed
        await db.commit()


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


async def save_task_metrics(
    assignment_id: uuid.UUID, task_id: uuid.UUID, session_id: uuid.UUID, metrics: AdaptorMetrics
) -> DbTaskMetrics:
    factory = get_session_factory()
    async with factory() as db:
        m = DbTaskMetrics(
            assignment_id=assignment_id,
            task_id=task_id,
            session_id=session_id,
            latency_ms=metrics.timing.latency_ms,
            time_to_first_token_ms=metrics.timing.time_to_first_token_ms,
            tokens_input=metrics.tokens.input,
            tokens_output=metrics.tokens.output,
            tokens_total=metrics.tokens.total,
            tokens_estimated=True,
            token_deviation=metrics.tokens.deviation,
            artifacts_created=metrics.artifacts.created_count,
            artifacts_updated=metrics.artifacts.updated_count,
            artifacts_deleted=metrics.artifacts.deleted_count,
        )
        db.add(m)
        await db.commit()
        await db.refresh(m)
        return m


async def get_session_metrics(session_id: uuid.UUID) -> list[DbTaskMetrics]:
    factory = get_session_factory()
    async with factory() as db:
        r = await db.execute(
            select(DbTaskMetrics).where(DbTaskMetrics.session_id == session_id)
        )
        return list(r.scalars().all())


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------


async def create_checkpoint(
    session_id: uuid.UUID,
    checkpoint_type: CheckpointType,
    presented: dict[str, Any],
    *,
    task_id: uuid.UUID | None = None,
) -> DbCheckpoint:
    factory = get_session_factory()
    async with factory() as db:
        c = DbCheckpoint(
            session_id=session_id,
            task_id=task_id,
            type=checkpoint_type,
            status=CheckpointStatus.pending,
            presented=presented,
            response=None,
            created_at=_now(),
        )
        db.add(c)
        await db.commit()
        await db.refresh(c)
        return c


async def resolve_checkpoint(
    checkpoint_id: uuid.UUID, status: CheckpointStatus, response: dict[str, Any] | None
) -> None:
    factory = get_session_factory()
    async with factory() as db:
        c = await db.get(DbCheckpoint, checkpoint_id)
        if c is None:
            raise KeyError("checkpoint not found")
        c.status = status
        c.response = response
        c.resolved_at = _now()
        await db.commit()


async def get_pending_checkpoint(session_id: uuid.UUID) -> DbCheckpoint | None:
    factory = get_session_factory()
    async with factory() as db:
        r = await db.execute(
            select(DbCheckpoint)
            .where(
                DbCheckpoint.session_id == session_id,
                DbCheckpoint.status == CheckpointStatus.pending,
            )
            .order_by(DbCheckpoint.created_at.asc())
            .limit(1)
        )
        return r.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


async def save_artifacts(
    assignment_id: uuid.UUID, task_id: uuid.UUID, session_id: uuid.UUID, artifacts: AdaptorArtifacts
) -> list[DbArtifact]:
    factory = get_session_factory()
    records: list[DbArtifact] = []
    now = _now()
    async with factory() as db:
        for p in artifacts.created:
            records.append(
                DbArtifact(
                    assignment_id=assignment_id,
                    task_id=task_id,
                    session_id=session_id,
                    path=p,
                    operation=ArtifactOperation.created,
                    created_at=now,
                )
            )
        for p in artifacts.updated:
            records.append(
                DbArtifact(
                    assignment_id=assignment_id,
                    task_id=task_id,
                    session_id=session_id,
                    path=p,
                    operation=ArtifactOperation.updated,
                    created_at=now,
                )
            )
        for p in artifacts.deleted:
            records.append(
                DbArtifact(
                    assignment_id=assignment_id,
                    task_id=task_id,
                    session_id=session_id,
                    path=p,
                    operation=ArtifactOperation.deleted,
                    created_at=now,
                )
            )
        for rec in records:
            db.add(rec)
        await db.commit()
        for rec in records:
            await db.refresh(rec)
        return records


async def get_task_artifacts(task_id: uuid.UUID) -> list[DbArtifact]:
    factory = get_session_factory()
    async with factory() as db:
        r = await db.execute(
            select(DbArtifact)
            .where(DbArtifact.task_id == task_id)
            .order_by(DbArtifact.created_at.asc())
        )
        return list(r.scalars().all())
