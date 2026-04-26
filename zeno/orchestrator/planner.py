from __future__ import annotations

import logging
from dataclasses import dataclass

import uuid

from zeno.agents.models import ExecutionPlanResponse, validate_lead_response
from zeno.db.models import AgentType, DbExecutionPlan, TaskType
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
    ) -> DbExecutionPlan:
        errs = validate_lead_response(response)
        if errs:
            raise ValidationError("Lead agent response failed validation", detail="\n".join(errs))

        logger.info(
            "Building plan | session_id=%s tasks=%d rooms=%d",
            session.id,
            len(response.tasks),
            len(response.rooms),
        )

        # 2) Create plan
        try:
            plan = await self.db_repo.create_execution_plan(session.id)
        except Exception as e:
            raise StorageError("Failed to create execution plan", detail=str(e)) from e

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

        # 4) Local id -> UUID mapping
        # 5) Create tasks
        created_task_uuid_by_local: dict[str, uuid.UUID] = {}
        for idx, t in enumerate(response.tasks):
            try:
                db_task = await self.db_repo.create_task(
                    plan_id=plan.id,
                    session_id=session.id,
                    title=t.title,
                    description=t.description,
                    task_type=TaskType(t.type),
                    priority=idx + 1,
                    parallel_group=t.parallel_group,
                    checkpoint_before=bool(t.checkpoint_before),
                )
            except Exception as e:
                raise StorageError(f"Failed to create task {t.id}", detail=str(e)) from e

            created_task_uuid_by_local[t.id] = db_task.id
            logger.debug(
                "Task created | id=%s title=%s type=%s agent_type=%s",
                t.id,
                t.title,
                t.type,
                t.agent_type,
            )

        # 6) Dependencies
        for t in response.tasks:
            for dep_local in t.depends_on:
                try:
                    await self.db_repo.add_task_dependency(
                        created_task_uuid_by_local[t.id],
                        created_task_uuid_by_local[dep_local],
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
                        agent_type=AgentType(t.agent_type),
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

        # 9) Mark plan active
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
                    agent_type=AgentType.lead,
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

