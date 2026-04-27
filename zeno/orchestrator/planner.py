from __future__ import annotations

import logging
from dataclasses import dataclass

import uuid

from zeno.agents.models import ExecutionPlanResponse, validate_lead_response
from zeno.db.models import DbExecutionPlan
from zeno.memory.models import MemTrace
from zeno.memory.store import save_trace
from zeno.orchestrator.errors import StorageError, ValidationError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutionPlanner:
    db_repo: object
    working_directory: str
    vault_name: str

    async def build_plan(
        self,
        response: ExecutionPlanResponse,
        *,
        session,
        plan: DbExecutionPlan | None = None,
        task_id_map: dict[str, uuid.UUID] | None = None,
    ) -> DbExecutionPlan:
        id_map = task_id_map if task_id_map is not None else {}
        errs = validate_lead_response(response, known_task_ids=set(id_map.keys()))
        if errs:
            raise ValidationError("Lead agent response failed validation", detail="\n".join(errs))

        logger.info(
            "Building plan | session_id=%s tasks=%d rooms=%d append=%s",
            session.id,
            len(response.tasks),
            len(response.rooms),
            plan is not None,
        )
        activate_new_plan = plan is None

        # 2) Create plan (or reuse for lazy chunk append)
        try:
            if plan is None:
                plan = await self.db_repo.create_execution_plan(session.id)
        except Exception as e:
            raise StorageError("Failed to create execution plan", detail=str(e)) from e

        try:
            existing_tasks = await self.db_repo.get_tasks_by_plan(plan.id)
            priority_base = max((t.priority for t in existing_tasks), default=0)
        except Exception as e:
            raise StorageError("Failed to read existing tasks for plan", detail=str(e)) from e

        # 3) Ensure rooms exist (SQLite rooms table is vault-scoped).
        try:
            vault = await self.db_repo.get_vault_by_path(session.working_directory)
            if vault is None:
                raise StorageError("Vault not found for working directory", detail=session.working_directory)
            for r in response.rooms:
                if not await self.db_repo.room_exists(vault.id, r.name):
                    await self.db_repo.create_room(vault_id=vault.id, name=r.name, description=r.description)
                    logger.debug("Room created | name=%s", r.name)
        except Exception as e:
            raise StorageError("Failed to create rooms", detail=str(e)) from e

        # 4) Local id -> UUID mapping (chunk tasks + task_id_map for cross-chunk deps)
        # 5) Create tasks
        created_task_uuid_by_local: dict[str, uuid.UUID] = {}
        for idx, t in enumerate(response.tasks):
            try:
                db_task = await self.db_repo.create_task(
                    plan_id=plan.id,
                    session_id=session.id,
                    title=t.title,
                    description=t.description,
                    task_type=t.type,
                    priority=priority_base + idx + 1,
                    parallel_group=t.parallel_group,
                    checkpoint_before=bool(t.checkpoint_before),
                )
            except Exception as e:
                raise StorageError(f"Failed to create task {t.id}", detail=str(e)) from e

            created_task_uuid_by_local[t.id] = db_task.id
            id_map[t.id] = db_task.id
            logger.debug(
                "Task created | id=%s title=%s type=%s agent_type=%s",
                t.id,
                t.title,
                t.type,
                t.agent_type,
            )

        # 6) Dependencies (within chunk + prior chunks via id_map)
        for t in response.tasks:
            for dep_local in t.depends_on:
                dep_uuid = id_map.get(dep_local)
                if dep_uuid is None:
                    raise StorageError(
                        f"Task {t.id} depends_on unknown id {dep_local!r}",
                        detail="dependency must reference a task id from this or a prior chunk",
                    )
                try:
                    await self.db_repo.add_task_dependency(
                        created_task_uuid_by_local[t.id],
                        dep_uuid,
                    )
                    logger.debug("Task dependency | %s depends_on %s", t.id, dep_local)
                except Exception as e:
                    raise StorageError(f"Failed to add dependency {t.id} -> {dep_local}", detail=str(e)) from e

        # 7) Create agents (unique agent_type; provider is fixed for now)
        agent_id_by_key: dict[str, uuid.UUID] = {}
        for t in response.tasks:
            key = t.agent_type
            if key in agent_id_by_key:
                continue

            # Stable name to avoid duplicates across runs.
            agent_name = f"{t.agent_type}-agent"
            try:
                existing = await self.db_repo.get_agent_by_name(agent_name)
                if existing is None:
                    existing = await self.db_repo.create_agent(
                        name=agent_name,
                        agent_type=t.agent_type,
                        system_prompt=_placeholder_system_prompt(t.agent_type),
                    )
                agent_id_by_key[key] = existing.id
            except Exception as e:
                raise StorageError("Failed to create/get agent", detail=str(e)) from e

        # 8) Assignments
        for t in response.tasks:
            try:
                agent_id = agent_id_by_key[t.agent_type]
                await self.db_repo.create_assignment(
                    task_id=created_task_uuid_by_local[t.id],
                    session_id=session.id,
                    agent_id=agent_id,
                )
            except Exception as e:
                raise StorageError(f"Failed to create assignment for task {t.id}", detail=str(e)) from e

        # 9) Mark plan active (new plan only — appended chunks reuse active plan)
        if activate_new_plan:
            try:
                await self.db_repo.activate_plan(plan.id)
            except Exception as e:
                raise StorageError("Failed to activate plan", detail=str(e)) from e

        logger.info("Plan built | plan_id=%s tasks=%d", plan.id, len(response.tasks))

        # 10) Save lead log to Chroma
        try:
            # Use room of first foundational task, or fallback.
            room = "default"
            for t in response.tasks:
                if t.type == "foundational":
                    room = t.room
                    break
            first_task_uuid = next(iter(created_task_uuid_by_local.values()))

            lead_agent_name = "lead"
            lead_agent = await self.db_repo.get_agent_by_name(lead_agent_name)
            if lead_agent is None:
                lead_agent = await self.db_repo.create_agent(
                    name=lead_agent_name,
                    agent_type="lead",
                    system_prompt=_placeholder_system_prompt("lead"),
                )

            trace = MemTrace(
                vault=self.vault_name,
                room=room,
                session_id=session.id,
                task_id=first_task_uuid,
                agent_type="lead",
                agent_id=str(lead_agent.id),
                content=response.log,
            )
            save_trace(self.working_directory, trace, agent_id=str(lead_agent.id))
        except Exception as e:
            raise StorageError("Failed to save lead log", detail=str(e)) from e

        return plan


def _placeholder_system_prompt(agent_type: str) -> str:
    return (
        f"You are Zeno's {agent_type} agent. "
        "Follow the task instructions and modify the repository as needed. "
        "Respond with valid JSON matching AgentResponse."
    )

