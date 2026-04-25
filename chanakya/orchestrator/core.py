from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from rich.console import Console

from chanakya.agents.models import CheckpointContent
from chanakya.agents.registry import AdaptorRegistry
from chanakya.cli import display as cli_display
from chanakya.core.config import load_config
from chanakya.core.enums import ExecutionMode, OrchestratorState
from chanakya.core.mode import OperationMode
from chanakya.db import repository as db_repository
from chanakya.db.engine import dispose_db_engine
from chanakya.db.models import (
    AgentMode,
    AgentType,
    DbSession,
    DbTask,
    Provider,
    SessionStatus,
    TaskType,
)
from chanakya.memory.palace import initialize_wing as initialize_mem_wing
from chanakya.memory.store import save_drawer
from chanakya.orchestrator import git as git_ops
from chanakya.orchestrator.dispatch import dispatch_agent
from chanakya.orchestrator.errors import (
    ChanakyaError,
    InitializationError,
    UnknownError,
    persist_session_failure,
)
from chanakya.orchestrator.session import initialize_session, prepare_workspace, teardown_session

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
        execution_mode: ExecutionMode,
        operation_mode: OperationMode,
        working_directory: str,
        hitl_callback: Callable[[CheckpointContent], Awaitable[str]] | None = None,
    ) -> None:
        self.execution_mode = execution_mode
        self.operation_mode = operation_mode
        self.working_directory = str(Path(working_directory).resolve())
        self.hitl_callback = hitl_callback

        self.current_session: DbSession | None = None
        self._wing: MemWing | None = None

        self.registry = AdaptorRegistry.discover()
        self.db_repo = db_repository
        self._sm = _StateMachine.phase6()

        self._rich_console = Console()

    async def initialize_runtime(self) -> None:
        if self.operation_mode == OperationMode.adapter and not self.registry.available():
            raise InitializationError("No adaptors available (is `gemini` on PATH?)")
        wing_name, _wd = await prepare_workspace(self.working_directory, db_repo=self.db_repo)
        self._wing = await initialize_mem_wing(self.working_directory)
        if self._wing.name != wing_name:
            logger.warning("wing name mismatch: %s vs %s", self._wing.name, wing_name)

    @property
    def wing_name(self) -> str:
        if self._wing is None:
            raise InitializationError("Orchestrator runtime not initialized")
        return self._wing.name

    async def _transition(self, new_state: OrchestratorState) -> None:
        assert self.current_session is not None
        old_state = await self.db_repo.get_orchestrator_state(self.current_session.id)

        if not self._sm.can_transition(old_state, new_state):
            raise UnknownError(
                f"Illegal orchestrator state transition: {old_state} -> {new_state}",
                detail=f"session_id={self.current_session.id}",
            )

        await self.db_repo.update_orchestrator_state(self.current_session.id, new_state)
        cli_display.print_state_transition(self._rich_console, new_state)

    async def _create_checkpoint(self, content: CheckpointContent) -> str:
        if self.hitl_callback is None:
            raise UnknownError(
                "HITL checkpoint requested but no callback is configured",
                detail="execution_mode or hitl_callback mismatch",
            )
        return await self.hitl_callback(content)

    async def _hitl_checkpoint_for_tasks(self, tasks: list[DbTask]) -> None:
        if self.execution_mode != ExecutionMode.HITL or self.hitl_callback is None:
            return
        for t in tasks:
            if not getattr(t, "checkpoint_before", False):
                continue
            content = CheckpointContent(
                type="pre_fanout",
                title=f"Before task: {getattr(t, 'title', '')}",
                description="This task is marked checkpoint_before. Choose how to proceed.",
                options=[],
            )
            await self._create_checkpoint(content)

    async def run(self, raw_input: str) -> bool:
        """
        Run one user prompt (one DbSession). Returns True on success, False on failure
        (errors are logged and printed; session is marked failed in SQLite).
        """
        if self._wing is None:
            raise InitializationError("Call initialize_runtime() before run()")

        t0 = perf_counter()
        self.current_session = None
        metrics = None
        success = False
        try:
            session, _wing_name = await initialize_session(
                raw_input=raw_input,
                execution_mode=self.execution_mode,
                working_directory=self.working_directory,
                db_repo=self.db_repo,
            )
            self.current_session = session

            st = await self.db_repo.get_orchestrator_state(session.id)
            cli_display.print_state_transition(self._rich_console, st)

            cli_display.print_progress_note(self._rich_console, "Lead agent complete", ok=True)

            await self._transition(OrchestratorState.EXECUTING)

            plan = await self.db_repo.create_execution_plan(session.id)
            task = await self.db_repo.create_task(
                plan_id=plan.id,
                session_id=session.id,
                title="Bare orchestrator task",
                description=raw_input,
                task_type=TaskType.implementation,
                priority=1,
            )

            await self._hitl_checkpoint_for_tasks([task])

            cli_display.print_task_activity(
                self._rich_console,
                task_title=task.title,
                agent_type="coding",
                status="running",
            )

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
                session_id=session.id,
                agent_id=agent.id,
            )

            worktree_path, branch_name = await git_ops.create_worktree(
                self.working_directory, session.id, task.id
            )
            await self.db_repo.assign_worktree(task.id, worktree_path=worktree_path, branch_name=branch_name)

            loaded = load_config()
            timeout_s = float(getattr(loaded.settings, "orchestrator_timeout_seconds", 60.0))
            response, metrics_out, drawer = await dispatch_agent(
                task=task,
                agent=agent,
                assignment=assignment,
                session=session,
                wing=self._wing,
                db_repo=self.db_repo,
                operation_mode=self.operation_mode,
                timeout_seconds=timeout_s,
            )
            metrics = metrics_out

            await self.db_repo.save_task_metrics(
                assignment_id=assignment.id,
                task_id=task.id,
                session_id=session.id,
                metrics=metrics_out,
            )
            await self.db_repo.save_artifacts(
                assignment_id=assignment.id,
                task_id=task.id,
                session_id=session.id,
                artifacts=response.artifacts,
            )
            save_drawer(self.working_directory, drawer, agent_id=str(agent.id))
            await self.db_repo.complete_assignment(assignment.id)
            await self.db_repo.complete_task(task.id, result_summary="completed")

            cli_display.print_task_activity(
                self._rich_console,
                task_title=task.title,
                agent_type="coding",
                status="completed",
            )

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

            await self._complete_session()

            elapsed = perf_counter() - t0
            tok_total = metrics.tokens.total if metrics and metrics.tokens else None
            cli_display.print_completion_summary(
                self._rich_console,
                task_count=1,
                files_created=len(response.artifacts.created),
                files_updated=len(response.artifacts.updated),
                files_deleted=len(response.artifacts.deleted),
                tokens_total=tok_total,
                elapsed_s=elapsed,
            )
            success = True

        except ChanakyaError as e:
            sid = self.current_session.id if self.current_session else None
            await persist_session_failure(e, sid, self.db_repo)
            cli_display.print_error(self._rich_console, e)
        except Exception as e:
            err = UnknownError(str(e), detail=repr(e))
            sid = self.current_session.id if self.current_session else None
            await persist_session_failure(err, sid, self.db_repo)
            cli_display.print_error(self._rich_console, err)
        finally:
            self.current_session = None
        return success

    async def _complete_session(self) -> None:
        assert self.current_session is not None
        await self._transition(OrchestratorState.COMPLETED)
        await teardown_session(
            self.current_session.id,
            status=SessionStatus.completed,
            orchestrator_state=OrchestratorState.COMPLETED,
            db_repo=self.db_repo,
        )

    async def teardown(self) -> None:
        if self.current_session is not None:
            sid = self.current_session.id
            try:
                tasks = await self.db_repo.get_tasks_with_worktrees(sid)
                for t in tasks:
                    if t.worktree_path and t.branch_name:
                        await git_ops.cleanup_worktree(
                            self.working_directory,
                            worktree_path=t.worktree_path,
                            branch_name=t.branch_name,
                        )
            except Exception as e:
                logger.warning("worktree cleanup during teardown: %s", str(e))
            try:
                await teardown_session(
                    sid,
                    status=SessionStatus.aborted,
                    orchestrator_state=OrchestratorState.ABORTED,
                    db_repo=self.db_repo,
                )
            except Exception as e:
                logger.warning("session teardown update failed: %s", str(e))
            self.current_session = None

        await dispose_db_engine()
