from __future__ import annotations

from dataclasses import dataclass

from chanakya.agents.models import LeadAgentResponse, validate_lead_response
from chanakya.db.models import AgentMode, AgentType, DbExecutionPlan, Provider, TaskType
from chanakya.memory.models import MemDrawer
from chanakya.memory.store import save_drawer
from chanakya.orchestrator.errors import StorageError, ValidationError


@dataclass(frozen=True)
class ExecutionPlanner:
    db_repo: object
    working_directory: str
    wing_name: str

    async def build_plan(
        self,
        response: LeadAgentResponse,
        *,
        session,
        available_providers: list[str],
    ) -> DbExecutionPlan:
        errs = validate_lead_response(response, available_providers=available_providers)
        if errs:
            raise ValidationError("Lead agent response failed validation", detail="\n".join(errs))

        if response.rooms is None or response.tasks is None or response.diary_entry is None:
            raise ValidationError("Lead agent response missing required execution fields")

        # 2) Create plan
        try:
            plan = await self.db_repo.create_execution_plan(session.id)
        except Exception as e:
            raise StorageError("Failed to create execution plan", detail=str(e)) from e

        # 3) Ensure rooms exist (SQLite rooms table is wing-scoped).
        try:
            wing = await self.db_repo.get_wing_by_path(session.working_directory)
            if wing is None:
                raise StorageError("Wing not found for working directory", detail=session.working_directory)
            for r in response.rooms:
                if not await self.db_repo.room_exists(wing.id, r.name):
                    await self.db_repo.create_room(wing_id=wing.id, name=r.name, description=r.description)
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

        # 6) Dependencies
        for t in response.tasks:
            for dep_local in t.depends_on:
                try:
                    await self.db_repo.add_task_dependency(
                        created_task_uuid_by_local[t.id],
                        created_task_uuid_by_local[dep_local],
                    )
                except Exception as e:
                    raise StorageError(f"Failed to add dependency {t.id} -> {dep_local}", detail=str(e)) from e

        # 7) Create agents (unique agent_type/provider)
        agent_id_by_key: dict[tuple[str, str], uuid.UUID] = {}
        for t in response.tasks:
            key = (t.agent_type, t.provider)
            if key in agent_id_by_key:
                continue

            # Stable name to avoid duplicates across runs.
            agent_name = f"{t.provider}-{t.agent_type}"
            try:
                existing = await self.db_repo.get_agent_by_name(agent_name)
                if existing is None:
                    existing = await self.db_repo.create_agent(
                        name=agent_name,
                        agent_type=AgentType(t.agent_type),
                        system_prompt=_placeholder_system_prompt(t.agent_type),
                        provider=Provider(t.provider),
                        mode=AgentMode.adapter,
                    )
                agent_id_by_key[key] = existing.id
            except Exception as e:
                raise StorageError("Failed to create/get agent", detail=str(e)) from e

        # 8) Assignments
        for t in response.tasks:
            try:
                agent_id = agent_id_by_key[(t.agent_type, t.provider)]
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

        # 10) Save lead diary entry to Chroma
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
                    provider=Provider("gemini"),
                    mode=AgentMode.adapter,
                )

            drawer = MemDrawer(
                wing=self.wing_name,
                room=room,
                session_id=session.id,
                task_id=first_task_uuid,
                agent_type="lead",
                agent_id=str(lead_agent.id),
                content=response.diary_entry,
            )
            save_drawer(self.working_directory, drawer, agent_id=str(lead_agent.id))
        except Exception as e:
            raise StorageError("Failed to save lead diary entry", detail=str(e)) from e

        return plan


def _placeholder_system_prompt(agent_type: str) -> str:
    return (
        f"You are Chanakya's {agent_type} agent. "
        "Follow the task instructions and modify the repository as needed. "
        "Respond with valid JSON matching AgentResponse."
    )

