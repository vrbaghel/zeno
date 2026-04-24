from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from chanakya.arthashastra.registry import AdaptorRegistry
from chanakya.core.config import load_config
from chanakya.core.enums import OrchestratorState
from chanakya.core.mode import OperationMode
from chanakya.db import repository as db_repository
from chanakya.db.models import (
    AgentMode,
    AgentType,
    Provider,
    SessionMode,
    SessionStatus,
    TaskType,
)
from chanakya.memory.models import MemWing
from chanakya.memory.palace import initialize_wing as initialize_mem_wing
from chanakya.memory.store import save_drawer
from chanakya.orchestrator import git as git_ops
from chanakya.orchestrator.dispatch import dispatch_agent
from chanakya.orchestrator.errors import (
    ChanakyaError,
    InitializationError,
    UnknownError,
    terminate,
)
from chanakya.orchestrator.session import initialize_session, teardown_session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _StateMachine:
    allowed: dict[OrchestratorState, set[OrchestratorState]]

    @classmethod
    def phase6(cls) -> "_StateMachine":
        return cls(
            allowed={
                OrchestratorState.INITIALIZING: {OrchestratorState.AWAITING_LEAD, OrchestratorState.FAILED},
                OrchestratorState.AWAITING_LEAD: {OrchestratorState.EXECUTING, OrchestratorState.FAILED},
                OrchestratorState.EXECUTING: {OrchestratorState.MERGING, OrchestratorState.FAILED},
                OrchestratorState.MERGING: {OrchestratorState.COMPLETED, OrchestratorState.FAILED},
                OrchestratorState.COMPLETED: set(),
                OrchestratorState.FAILED: set(),
                OrchestratorState.ABORTED: set(),
                OrchestratorState.AWAITING_HUMAN: set(),
                OrchestratorState.PLANNING: set(),
            }
        )

    def can_transition(self, old: OrchestratorState, new: OrchestratorState) -> bool:
        return new in self.allowed.get(old, set())


class OrchestratorCore:
    def __init__(
        self,
        *,
        raw_input: str,
        execution_mode: SessionMode,
        operation_mode: OperationMode,
        working_directory: str,
    ) -> None:
        self.raw_input = raw_input
        self.execution_mode = execution_mode
        self.operation_mode = operation_mode
        self.working_directory = str(Path(working_directory).resolve())

        self.session = None
        self._wing: MemWing | None = None

        self.registry = AdaptorRegistry.discover()
        self.db_repo = db_repository

        self._sm = _StateMachine.phase6()
        self._console = Console()

    async def initialize(self) -> None:
        if not self.registry.available():
            raise InitializationError("No adaptors available (is `gemini` on PATH?)")

        # `initialize_session` already creates the SQLite wing record; it returns wing_name.
        session, wing_name = await initialize_session(
            raw_input=self.raw_input,
            execution_mode=self.execution_mode,
            working_directory=self.working_directory,
            db_repo=self.db_repo,
        )
        self.session = session

        # Keep a MemWing handle for dispatch/memory writes.
        self._wing = await initialize_mem_wing(self.working_directory)
        if self._wing.name != wing_name:
            logger.warning("wing name mismatch: %s vs %s", self._wing.name, wing_name)

    async def run(self) -> None:
        try:
            await self.initialize()
            await self._execute_single_task()
            await self._complete_session()
        except ChanakyaError as e:
            sid = None if self.session is None else self.session.id
            await terminate(e, sid, self.db_repo)
        except Exception as e:
            sid = None if self.session is None else self.session.id
            await terminate(UnknownError(str(e), detail=repr(e)), sid, self.db_repo)

    async def _transition(self, new_state: OrchestratorState) -> None:
        assert self.session is not None
        old_state = await self.db_repo.get_orchestrator_state(self.session.id)

        if not self._sm.can_transition(old_state, new_state):
            raise UnknownError(
                f"Illegal orchestrator state transition: {old_state} -> {new_state}",
                detail=f"session_id={self.session.id}",
            )

        await self.db_repo.update_orchestrator_state(self.session.id, new_state)

    async def _execute_single_task(self) -> None:
        assert self.session is not None
        assert self._wing is not None

        await self._transition(OrchestratorState.EXECUTING)

        # Minimal plan+task for Phase 6.
        plan = await self.db_repo.create_execution_plan(self.session.id)
        task = await self.db_repo.create_task(
            plan_id=plan.id,
            session_id=self.session.id,
            title="Bare orchestrator task",
            description=self.raw_input,
            task_type=TaskType.implementation,
            priority=1,
        )

        # Hardcoded test agent (until lead agent exists).
        agent_name = "phase6-test-agent"
        agent = await self.db_repo.get_agent_by_name(agent_name)
        if agent is None:
            agent = await self.db_repo.create_agent(
                name=agent_name,
                agent_type=AgentType.coding,
                system_prompt=(
                    "You are Chanakya's test coding agent. "
                    "You must do the user's request by modifying the repository. "
                    "Return a JSON object that conforms to AgentResponse."
                ),
                provider=Provider.gemini,
                mode=AgentMode(self.operation_mode.value),
            )

        assignment = await self.db_repo.create_assignment(
            task_id=task.id,
            session_id=self.session.id,
            agent_id=agent.id,
        )

        # Worktree lifecycle.
        worktree_path, branch_name = await git_ops.create_worktree(
            self.working_directory, self.session.id, task.id
        )
        await self.db_repo.assign_worktree(task.id, worktree_path=worktree_path, branch_name=branch_name)

        # Dispatch agent.
        loaded = load_config()
        timeout_s = float(getattr(loaded.settings, "orchestrator_timeout_seconds", 60.0))
        response, metrics, drawer = await dispatch_agent(
            task=task,
            agent=agent,
            assignment=assignment,
            session=self.session,
            wing=self._wing,
            db_repo=self.db_repo,
            operation_mode=self.operation_mode,
            timeout_seconds=timeout_s,
        )

        # Persist outputs.
        await self.db_repo.save_task_metrics(
            assignment_id=assignment.id,
            task_id=task.id,
            session_id=self.session.id,
            metrics=metrics,
        )
        await self.db_repo.save_artifacts(
            assignment_id=assignment.id,
            task_id=task.id,
            session_id=self.session.id,
            artifacts=response.artifacts,
        )
        save_drawer(self.working_directory, drawer, agent_id=str(agent.id))
        await self.db_repo.complete_assignment(assignment.id)
        await self.db_repo.complete_task(task.id, result_summary="completed")

        await self._transition(OrchestratorState.MERGING)

        await git_ops.merge_worktree(
            self.working_directory,
            branch_name=branch_name,
            task_title=task.title,
        )
        await git_ops.cleanup_worktree(
            self.working_directory,
            worktree_path=worktree_path,
            branch_name=branch_name,
        )
        await self.db_repo.clear_worktree(task.id)

    async def _complete_session(self) -> None:
        assert self.session is not None
        await self._transition(OrchestratorState.COMPLETED)
        await teardown_session(
            self.session.id,
            status=SessionStatus.completed,
            orchestrator_state=OrchestratorState.COMPLETED,
            db_repo=self.db_repo,
        )
        self._console.print("[green]Session completed.[/green]")

