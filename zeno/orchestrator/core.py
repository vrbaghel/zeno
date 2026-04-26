from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from uuid import UUID

from rich.console import Console

from zeno.agents.lead.adapter import LeadAgentAdapter, LeadAgentContext
from zeno.agents.models import (
    AgentContext,
    CheckpointContent,
    CheckpointOption,
    ClarificationAnswer,
    ClarificationQuestion,
    ExecutionPlanResponse,
)
from zeno.agents.worker.adapter import WorkerAdapter
from zeno.cli import display as cli_display
from zeno.core.enums import ExecutionMode, OrchestratorState
from zeno.db import repository as db_repository
from zeno.db.engine import dispose_db_engine
from zeno.db.models import AgentType, DbExecutionPlan, DbSession, DbTask, SessionStatus, TaskStatus
from zeno.memory.models import MemVault
from zeno.memory.mind import initialize_vault as initialize_mem_vault
from zeno.memory.retrieval import build_context
from zeno.memory.store import save_trace
from zeno.orchestrator import git as git_ops
from zeno.orchestrator.errors import (
    DispatchError,
    InitializationError,
    WorkerTerminationError,
    StorageError,
    UnknownError,
    ValidationError,
    ZenoError,
    persist_session_failure,
)
from zeno.orchestrator.session import initialize_session, prepare_workspace, teardown_session
from zeno.orchestrator.planner import ExecutionPlanner

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

    @classmethod
    def default(cls) -> "_StateMachine":
        return cls(
            allowed={
                OrchestratorState.INITIALIZING: {
                    OrchestratorState.AWAITING_LEAD,
                    OrchestratorState.FAILED,
                },
                OrchestratorState.AWAITING_LEAD: {
                    OrchestratorState.AWAITING_HUMAN,
                    OrchestratorState.PLANNING,
                    OrchestratorState.FAILED,
                },
                OrchestratorState.AWAITING_HUMAN: {
                    OrchestratorState.AWAITING_LEAD,
                    OrchestratorState.PLANNING,
                    OrchestratorState.EXECUTING,
                    OrchestratorState.ABORTED,
                    OrchestratorState.FAILED,
                },
                OrchestratorState.PLANNING: {
                    OrchestratorState.EXECUTING,
                    OrchestratorState.FAILED,
                },
                OrchestratorState.EXECUTING: {
                    OrchestratorState.AWAITING_LEAD,
                    OrchestratorState.MERGING,
                    OrchestratorState.COMPLETED,
                    OrchestratorState.FAILED,
                },
                OrchestratorState.MERGING: {
                    OrchestratorState.EXECUTING,
                    OrchestratorState.COMPLETED,
                    OrchestratorState.FAILED,
                },
                OrchestratorState.COMPLETED: set(),
                OrchestratorState.FAILED: set(),
                OrchestratorState.ABORTED: set(),
            }
        )

    def can_transition(self, old: OrchestratorState, new: OrchestratorState) -> bool:
        return new in self.allowed.get(old, set())


class OrchestratorCore:
    def __init__(
        self,
        *,
        execution_mode: ExecutionMode,
        working_directory: str,
        hitl_callback: Callable[[CheckpointContent], Awaitable[str]] | None = None,
    ) -> None:
        self.execution_mode = execution_mode
        self.working_directory = str(Path(working_directory).resolve())
        self.hitl_callback = hitl_callback

        self.current_session: DbSession | None = None
        self._vault: MemVault | None = None
        self._vault_name: str | None = None

        self.lead_adapter: LeadAgentAdapter | None = None
        self.worker_adapter: WorkerAdapter | None = None

        self.db_repo = db_repository
        self._sm = _StateMachine.default()
        self._rich_console = Console()

    async def initialize_runtime(self) -> None:
        vault_name, _wd = await prepare_workspace(self.working_directory, db_repo=self.db_repo)
        self._vault = await initialize_mem_vault(self.working_directory)
        self._vault_name = vault_name
        if self._vault.name != vault_name:
            logger.warning("vault name mismatch: %s vs %s", self._vault.name, vault_name)
        self.worker_adapter = WorkerAdapter(working_directory=self.working_directory)

    @property
    def vault_name(self) -> str:
        if self._vault is None or self._vault_name is None:
            raise InitializationError("Orchestrator runtime not initialized")
        return self._vault_name

    async def _transition(self, new_state: OrchestratorState) -> None:
        assert self.current_session is not None
        old_state = await self.db_repo.get_orchestrator_state(self.current_session.id)

        # Treat self-transitions as idempotent no-ops.
        if old_state == new_state:
            return

        if not self._sm.can_transition(old_state, new_state):
            raise UnknownError(
                f"Illegal orchestrator state transition: {old_state} -> {new_state}",
                detail=f"session_id={self.current_session.id}",
            )

        await self.db_repo.update_orchestrator_state(self.current_session.id, new_state)
        cli_display.print_state_transition(self._rich_console, new_state)

    async def run(self, raw_input: str) -> bool:
        if self._vault is None:
            raise InitializationError("Call initialize_runtime() before run()")
        if self.worker_adapter is None:
            raise InitializationError("Worker adapter not initialized")

        self.current_session = None
        success = False
        t0 = perf_counter()
        try:
            session = await initialize_session(
                raw_input=raw_input,
                execution_mode=self.execution_mode,
                working_directory=self.working_directory,
                db_repo=self.db_repo,
            )
            self.current_session = session

            st = await self.db_repo.get_orchestrator_state(session.id)
            cli_display.print_state_transition(self._rich_console, st)

            # Lead agent planning
            lead_plan = await self._dispatch_lead_agent(
                raw_input=raw_input,
                stage="initial",
                current_plan=None,
                completed_tasks=None,
                revision_reason=None,
            )

            await self._transition(OrchestratorState.PLANNING)

            planner = ExecutionPlanner(
                db_repo=self.db_repo,
                working_directory=self.working_directory,
                vault_name=self.vault_name,
            )
            plan = await planner.build_plan(lead_plan, session=session)

            # Persist lead SDK session id for later resumption (revision).
            if self.lead_adapter and self.lead_adapter.session_id:
                await self.db_repo.update_session_lead_session_id(session.id, self.lead_adapter.session_id)

            # HITL plan approval (A/B/C)
            if self.execution_mode == ExecutionMode.HITL and self.hitl_callback is not None:
                while True:
                    choice = await self._create_checkpoint(
                        CheckpointContent(
                            type="plan_approval",
                            title="Approve execution plan?",
                            description="A: Approve  B: Cancel  C: Modify",
                            options=[
                                CheckpointOption(key="a", label="Approve"),
                                CheckpointOption(key="b", label="Cancel"),
                                CheckpointOption(key="c", label="Modify"),
                            ],
                            payload={},
                        )
                    )
                    if choice == "a":
                        break
                    if choice == "b":
                        await teardown_session(
                            session.id,
                            status=SessionStatus.aborted,
                            orchestrator_state=OrchestratorState.ABORTED,
                            db_repo=self.db_repo,
                        )
                        return False
                    if choice == "c":
                        lead_plan = await self._revise_plan(
                            raw_input=raw_input,
                            current_plan=lead_plan,
                        )
                        plan = await planner.build_plan(lead_plan, session=session)
                        continue

            await self._transition(OrchestratorState.EXECUTING)
            await self._execute_plan(plan)
            await self._complete_session()

            elapsed = perf_counter() - t0
            # Metrics aggregation is not implemented yet; keep prior behavior.
            cli_display.print_completion_summary(
                self._rich_console,
                task_count=1,
                files_created=0,
                files_updated=0,
                files_deleted=0,
                tokens_total=None,
                elapsed_s=elapsed,
            )
            success = True

        except ZenoError as e:
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

    async def _create_checkpoint(self, content: CheckpointContent) -> str:
        if self.hitl_callback is None:
            raise UnknownError(
                "HITL checkpoint requested but no callback is configured",
                detail="execution_mode or hitl_callback mismatch",
            )
        await self._transition(OrchestratorState.AWAITING_HUMAN)
        choice = await self.hitl_callback(content)
        return choice

    async def _dispatch_lead_agent(
        self,
        *,
        raw_input: str,
        stage: str,  # "initial" | "revision"
        current_plan: ExecutionPlanResponse | None,
        completed_tasks: list[str] | None,
        revision_reason: str | None,
    ) -> ExecutionPlanResponse:
        if self._vault is None or self.current_session is None:
            raise InitializationError("Session not initialized")

        wd = self.working_directory
        vault_row = await self.db_repo.get_vault_by_path(wd)
        vault_id = vault_row.id if vault_row is not None else None
        existing_rooms: list[str] = []
        if vault_id is not None:
            try:
                rooms = await self.db_repo.get_rooms(vault_id)
                existing_rooms = [r.name for r in rooms]
            except Exception:
                existing_rooms = []

        # Build memory context for lead agent.
        try:
            current_tasks: list[DbTask] = []
            active_plan = await self.db_repo.get_active_plan(self.current_session.id)
            if active_plan is not None:
                current_tasks = await self.db_repo.get_tasks_by_plan(active_plan.id)
            mem_ctx = build_context(
                working_directory=wd,
                vault=self._vault,
                task_description=raw_input,
                agent_type="lead",
                agent_id="lead",
                session_id=self.current_session.id,
                current_session_tasks=current_tasks,
            )
        except Exception:
            mem_ctx = None

        agent_context = AgentContext(
            session_summary=str(getattr(mem_ctx, "session_summary", "") or "") if mem_ctx else "",
            relevant_traces=[d.to_document().strip() for d in getattr(mem_ctx, "relevant_traces", [])] if mem_ctx else [],
            agent_logs=[d.to_document().strip() for d in getattr(mem_ctx, "agent_logs", [])] if mem_ctx else [],
        )

        from zeno.core.enums import LeadAgentStage

        ctx = LeadAgentContext(
            session_id=str(self.current_session.id),
            raw_input=raw_input,
            mode=self.execution_mode,
            stage=LeadAgentStage.INITIAL if stage == "initial" else LeadAgentStage.REVISION,
            working_directory=wd,
            existing_rooms=existing_rooms,
            agent_context=agent_context,
            current_plan=current_plan,
            completed_tasks=completed_tasks,
            revision_reason=revision_reason,
        )

        if self.lead_adapter is None:
            self.lead_adapter = LeadAgentAdapter(
                execution_mode=self.execution_mode,
                working_directory=self.working_directory,
                hitl_callback=self._lead_hitl_callback if self.execution_mode == ExecutionMode.HITL else None,
            )

        await self._transition(OrchestratorState.AWAITING_LEAD)
        if stage == "initial":
            return await self.lead_adapter.dispatch(ctx)
        return await self.lead_adapter.revise(ctx)

    async def _lead_hitl_callback(
        self, questions: list[ClarificationQuestion]
    ) -> list[ClarificationAnswer]:
        answers: list[ClarificationAnswer] = []
        for q in questions:
            opts = q.options or []
            keys = ["a", "b", "c", "d"]
            content = CheckpointContent(
                type="unexpected",
                title=q.question,
                description="Choose one option.",
                options=[
                    CheckpointOption(key=keys[i], label=opts[i]) for i in range(min(len(opts), 4))
                ],
                payload={"question_id": q.id},
            )
            choice = await self._create_checkpoint(content)
            idx = keys.index(choice) if choice in keys else 0
            label = opts[idx] if idx < len(opts) else (opts[0] if opts else "")
            answers.append(ClarificationAnswer(question_id=q.id, answer=label))
        return answers

    async def _revise_plan(
        self,
        *,
        raw_input: str,
        current_plan: ExecutionPlanResponse,
    ) -> ExecutionPlanResponse:
        if self.hitl_callback is None:
            raise ValidationError("Revision requested but no HITL callback configured")
        reason = await cli_display.print_revision_prompt(self._rich_console)
        return await self._dispatch_lead_agent(
            raw_input=raw_input,
            stage="revision",
            current_plan=current_plan,
            completed_tasks=[],
            revision_reason=reason,
        )

    async def _execute_plan(self, plan: DbExecutionPlan) -> None:
        while True:
            runnable = await self.db_repo.get_runnable_tasks(plan.id)
            if not runnable:
                pending = await self.db_repo.get_pending_tasks(plan.id)
                if pending:
                    raise UnknownError("Dependency deadlock: no runnable tasks but pending tasks exist")
                return
            for task in runnable:
                await self._execute_task(plan=plan, task=task)

    async def _execute_task(self, *, plan: DbExecutionPlan, task: DbTask) -> None:
        assert self.current_session is not None
        if self._vault is None or self.worker_adapter is None:
            raise InitializationError("Runtime not initialized")

        await self.db_repo.update_task_status(task.id, TaskStatus.running)
        cli_display.print_task_activity(
            self._rich_console,
            task_title=task.title,
            agent_type=str(getattr(task, "agent_type", "coding")),
            status="running",
        )

        worktree_path, branch_name = await git_ops.create_worktree(
            self.working_directory, self.current_session.id, task.id
        )
        await self.db_repo.assign_worktree(task.id, worktree_path=worktree_path, branch_name=branch_name)

        # Get or create agent by type (agent_type string stored on DbAgent.type enum).
        agent_name = f"{getattr(task, 'agent_type', 'coding')}-agent"
        agent = await self.db_repo.get_agent_by_name(agent_name)
        if agent is None:
            agent = await self.db_repo.create_agent(
                name=agent_name,
                agent_type=AgentType.other,
                system_prompt="(dynamic)",
            )

        assignment = await self.db_repo.create_assignment(task_id=task.id, session_id=self.current_session.id, agent_id=agent.id)
        await self.db_repo.start_assignment(assignment.id)

        completed_tasks = await self.db_repo.get_completed_tasks(plan.id)
        mem_ctx = build_context(
            working_directory=self.working_directory,
            vault=self._vault,
            task_description=task.description,
            agent_type=str(getattr(task, "agent_type", "coding")),
            agent_id=str(agent.id),
            session_id=self.current_session.id,
            current_session_tasks=completed_tasks,
        )
        chroma_ctx = AgentContext(
            session_summary=str(getattr(mem_ctx, "session_summary", "") or ""),
            relevant_traces=[d.to_document().strip() for d in getattr(mem_ctx, "relevant_traces", [])],
            agent_logs=[d.to_document().strip() for d in getattr(mem_ctx, "agent_logs", [])],
        )

        # Dispatch worker in the worktree.
        self.worker_adapter.working_directory = worktree_path
        try:
            response, metrics = await self.worker_adapter.dispatch(
                task=task, agent=agent, chroma_context=chroma_ctx
            )
        except WorkerTerminationError as e:
            await self.db_repo.update_task_status(task.id, TaskStatus.failed)
            await self.db_repo.complete_assignment(assignment.id)
            await git_ops.cleanup_worktree(
                self.working_directory,
                worktree_path=worktree_path,
                branch_name=branch_name,
            )
            await self.db_repo.clear_worktree(task.id)
            raise DispatchError(
                f"Worker agent could not complete task: {task.title}",
                detail=str(e),
            ) from e

        await self.db_repo.save_task_metrics(
            assignment_id=assignment.id,
            task_id=task.id,
            session_id=self.current_session.id,
            metrics=metrics,
        )
        await self.db_repo.save_artifacts(
            assignment_id=assignment.id,
            task_id=task.id,
            session_id=self.current_session.id,
            artifacts=response.artifacts,
        )

        # Stage and commit all agent changes before merge.
        await git_ops.commit_worktree_changes(worktree_path=worktree_path, task_title=task.title)

        # Save worker trace
        from zeno.memory.models import MemTrace

        trace = MemTrace(
            vault=self.vault_name,
            room=str(getattr(task, "room", "default") or "default"),
            session_id=self.current_session.id,
            task_id=task.id,
            agent_type=str(getattr(task, "agent_type", "coding")),
            agent_id=str(agent.id),
            content=response.log,
        )
        save_trace(self.working_directory, trace, agent_id=str(agent.id))

        await self.db_repo.complete_assignment(assignment.id)
        await self.db_repo.complete_task(task.id, response.summary)

        await self._transition(OrchestratorState.MERGING)
        await git_ops.merge_worktree(self.working_directory, branch_name=branch_name, task_title=task.title)
        await git_ops.cleanup_worktree(self.working_directory, worktree_path=worktree_path, branch_name=branch_name)
        await self.db_repo.clear_worktree(task.id)

        cli_display.print_task_activity(
            self._rich_console,
            task_title=task.title,
            agent_type=str(getattr(task, "agent_type", "coding")),
            status="complete",
        )
        await self._transition(OrchestratorState.EXECUTING)

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
