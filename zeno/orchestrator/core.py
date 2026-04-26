from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
import uuid
from uuid import UUID

from rich.console import Console

from zeno.agents.lead.adapter import LeadAgentAdapter, LeadAgentContext
from zeno.agents.models import (
    AgentArtifacts,
    AgentContext,
    CheckpointContent,
    CheckpointOption,
    ClarificationAnswer,
    ClarificationQuestion,
    ExecutionPlanResponse,
    WorkerMetrics,
    WorkerResponse,
)
from zeno.agents.worker.adapter import WorkerAdapter
from zeno.cli import display as cli_display
from zeno.core.enums import ExecutionMode, OrchestratorState
from zeno.db import repository as db_repository
from zeno.db.engine import dispose_db_engine
from zeno.db.models import AgentType, DbExecutionPlan, DbSession, DbTask, PlanStatus, SessionStatus, TaskStatus
from zeno.memory.models import MemLog, MemTrace, MemVault
from zeno.memory.mind import initialize_vault as initialize_mem_vault
from zeno.memory.retrieval import build_context
from zeno.memory.store import save_trace
from zeno.orchestrator import git as git_ops
from zeno.orchestrator.errors import (
    DispatchError,
    InitializationError,
    ParseError,
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
                OrchestratorState.ABORTED: {
                    OrchestratorState.EXECUTING,
                },
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

        logger.debug("State transition | %s -> %s | session_id=%s", old_state, new_state, self.current_session.id)
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
            logger.info(
                "Session started | session_id=%s mode=%s wd=%s",
                session.id,
                self.execution_mode.value,
                self.working_directory,
            )

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
            logger.info(
                "Lead plan received | session_id=%s tasks=%d rooms=%d",
                session.id,
                len(getattr(lead_plan, "tasks", []) or []),
                len(getattr(lead_plan, "rooms", []) or []),
            )

            await self._transition(OrchestratorState.PLANNING)

            planner = ExecutionPlanner(
                db_repo=self.db_repo,
                working_directory=self.working_directory,
                vault_name=self.vault_name,
            )
            plan = await planner.build_plan(lead_plan, session=session)
            logger.info("Executing plan | session_id=%s plan_id=%s", session.id, plan.id)

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
            logger.info("Session complete | session_id=%s elapsed=%.1fs", session.id, elapsed)

        except ZenoError as e:
            sid = self.current_session.id if self.current_session else None
            logger.error("Session failed | session_id=%s code=%s msg=%s", sid, e.code, e.message)
            await persist_session_failure(e, sid, self.db_repo)
            cli_display.print_error(self._rich_console, e)
        except Exception as e:
            err = UnknownError(str(e), detail=repr(e))
            sid = self.current_session.id if self.current_session else None
            logger.error("Session failed | session_id=%s err=%s", sid, repr(e))
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
            # Attempt to resume the vault-wide lead session across CLI invocations.
            try:
                prior = await self.db_repo.get_latest_lead_session_id_for_vault(wd)
                if prior:
                    self.lead_adapter._session_id = prior  # resume seed
            except Exception:
                pass

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

            parallel_groups: dict[str, list[DbTask]] = {}
            sequential: list[DbTask] = []
            for task in runnable:
                pg = getattr(task, "parallel_group", None)
                if pg:
                    parallel_groups.setdefault(str(pg), []).append(task)
                else:
                    sequential.append(task)

            # Execute parallel groups stage-by-stage; within each stage dispatch concurrently,
            # then run a merge agent to integrate the group branches.
            for group_key, group_tasks in parallel_groups.items():
                logger.info(
                    "Parallel stage start | group=%s tasks=%d",
                    group_key,
                    len(group_tasks),
                )
                assert self.current_session is not None
                # 1) Create worktrees concurrently
                worktrees = await asyncio.gather(
                    *[
                        git_ops.create_worktree(self.working_directory, self.current_session.id, t.id)
                        for t in group_tasks
                    ]
                )
                for t, (worktree_path, branch_name) in zip(group_tasks, worktrees, strict=False):
                    await self.db_repo.assign_worktree(t.id, worktree_path=worktree_path, branch_name=branch_name)
                    logger.debug(
                        "Worktree created | task_id=%s path=%s branch=%s",
                        t.id,
                        worktree_path,
                        branch_name,
                    )

                # 2) Dispatch tasks concurrently (no per-task merge; merge happens in stage merge agent)
                results = await asyncio.gather(
                    *[
                        self._execute_task(
                            plan=plan,
                            task=t,
                            worktree_path=worktree_path,
                            branch_name=branch_name,
                            merge_immediately=False,
                        )
                        for t, (worktree_path, branch_name) in zip(group_tasks, worktrees, strict=False)
                    ],
                    return_exceptions=True,
                )

                failures: list[BaseException] = [
                    r for r in results if isinstance(r, BaseException)
                ]

                # If any task failed, skip merge agent and fail the stage (after cleanup).
                if failures:
                    for t, (worktree_path, branch_name) in zip(group_tasks, worktrees, strict=False):
                        try:
                            await git_ops.cleanup_worktree(
                                self.working_directory,
                                worktree_path=worktree_path,
                                branch_name=branch_name,
                            )
                        finally:
                            await self.db_repo.clear_worktree(t.id)
                    first = failures[0]
                    raise DispatchError(
                        f"Parallel group {group_key} failed",
                        detail=f"{type(first).__name__}: {first}",
                    ) from first

                # 3) Merge stage via merge agent (real worker) and then clean up all group worktrees/branches.
                logger.info("Parallel stage merge | group=%s branches=%d", group_key, len(worktrees))
                await self._merge_parallel_stage(
                    plan=plan,
                    stage_key=group_key,
                    branches=[bn for (_wp, bn) in worktrees],
                )
                for t, (worktree_path, branch_name) in zip(group_tasks, worktrees, strict=False):
                    await git_ops.cleanup_worktree(
                        self.working_directory, worktree_path=worktree_path, branch_name=branch_name
                    )
                    await self.db_repo.clear_worktree(t.id)
                logger.info("Parallel stage complete | group=%s", group_key)

            # Execute sequential tasks one at a time (includes merge).
            for task in sequential:
                await self._execute_task(plan=plan, task=task)

    async def _execute_task(
        self,
        *,
        plan: DbExecutionPlan,
        task: DbTask,
        worktree_path: str | None = None,
        branch_name: str | None = None,
        merge_immediately: bool = True,
    ) -> None:
        assert self.current_session is not None
        if self._vault is None or self.worker_adapter is None:
            raise InitializationError("Runtime not initialized")

        await self.db_repo.update_task_status(task.id, TaskStatus.running)
        logger.info(
            "Task started | task_id=%s title=%s",
            task.id,
            task.title,
        )
        cli_display.print_task_activity(
            self._rich_console,
            task_title=task.title,
            agent_type=str(getattr(task, "agent_type", "coding")),
            status="running",
        )

        if worktree_path is None or branch_name is None:
            worktree_path, branch_name = await git_ops.create_worktree(
                self.working_directory, self.current_session.id, task.id
            )
            await self.db_repo.assign_worktree(task.id, worktree_path=worktree_path, branch_name=branch_name)
            logger.debug("Worktree created | path=%s branch=%s task_id=%s", worktree_path, branch_name, task.id)

        # Use the planned assignment if present; otherwise fallback to creating one.
        assignment = await self.db_repo.get_assignment_for_task(task.id)
        if assignment is None:
            agent_name = "other-agent"
            agent = await self.db_repo.get_agent_by_name(agent_name)
            if agent is None:
                agent = await self.db_repo.create_agent(
                    name=agent_name,
                    agent_type=AgentType.other,
                    system_prompt="(dynamic)",
                )
            assignment = await self.db_repo.create_assignment(
                task_id=task.id, session_id=self.current_session.id, agent_id=agent.id
            )
        agent = await self.db_repo.get_agent(assignment.agent_id)
        await self.db_repo.start_assignment(assignment.id)

        completed_tasks = await self.db_repo.get_completed_tasks(plan.id)
        mem_ctx = None
        if completed_tasks:
            mem_ctx = build_context(
                working_directory=self.working_directory,
                vault=self._vault,
                task_description=task.description,
                agent_type=str(getattr(agent, "type", "coding")),
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
        _dispatch_queued_at = datetime.now(timezone.utc)
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
        except ParseError as e:
            # The worker completed its file work but could not produce valid structured
            # output even after reconciliation retries. Recover artifacts from git status
            # so the task can still commit, merge, and feed into downstream tasks.
            logger.warning(
                "Worker parse failed after reconciliation — recovering from worktree | "
                "task_id=%s title=%s err=%s",
                task.id,
                task.title,
                e,
            )
            _recovered_at = datetime.now(timezone.utc)
            _latency_ms = int((_recovered_at - _dispatch_queued_at).total_seconds() * 1000)
            created, updated, deleted = await git_ops.get_changed_files(worktree_path)
            response = WorkerResponse(
                type="success",
                summary=(
                    f"[recovered] {task.title}: structured output failed after retries, "
                    "artifacts recovered from git status"
                ),
                artifacts=AgentArtifacts(
                    created=created,
                    updated=updated,
                    deleted=deleted,
                ),
                log=MemLog(
                    summary=(
                        f"Recovered from ParseError. Original error: {e.message}. "
                        f"Detail: {e.detail or '(none)'}"
                    ),
                    decisions=[],
                    assumptions=[],
                    open_issues=[str(e.detail or e.message)],
                    room=str(getattr(task, "room", "default") or "default"),
                ),
            )
            metrics = WorkerMetrics(
                queued_at=_dispatch_queued_at,
                completed_at=_recovered_at,
                latency_ms=_latency_ms,
            )
        logger.info(
            "Task complete | task_id=%s title=%s tokens=%s cost=%s latency_ms=%s",
            task.id,
            task.title,
            getattr(metrics, "total_tokens", None),
            getattr(metrics, "cost_usd", None),
            getattr(metrics, "latency_ms", None),
        )

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

        if merge_immediately:
            await self._transition(OrchestratorState.MERGING)
            logger.info("Merging | branch=%s task_title=%s", branch_name, task.title)
            await git_ops.merge_worktree(
                self.working_directory, branch_name=branch_name, task_title=task.title
            )
            await git_ops.cleanup_worktree(
                self.working_directory, worktree_path=worktree_path, branch_name=branch_name
            )
            await self.db_repo.clear_worktree(task.id)
            logger.debug("Worktree cleaned up | path=%s", worktree_path)

        cli_display.print_task_activity(
            self._rich_console,
            task_title=task.title,
            agent_type=str(getattr(agent, "type", "coding")),
            status="complete",
        )
        if merge_immediately:
            await self._transition(OrchestratorState.EXECUTING)

    async def _merge_parallel_stage(self, *, plan: DbExecutionPlan, stage_key: str, branches: list[str]) -> None:
        """
        Run a merge agent to integrate a completed parallel group into main.
        """
        assert self.current_session is not None
        if self.worker_adapter is None:
            raise InitializationError("Worker adapter not initialized")

        merge_task_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"merge:{self.current_session.id}:{stage_key}")
        merge_worktree_path, merge_branch_name = await git_ops.create_worktree(
            self.working_directory, self.current_session.id, merge_task_id
        )

        # Get or create a stable merge agent.
        merge_agent_name = "merge-agent"
        merge_agent = await self.db_repo.get_agent_by_name(merge_agent_name)
        if merge_agent is None:
            merge_agent = await self.db_repo.create_agent(
                name=merge_agent_name,
                agent_type=AgentType.integration,
                system_prompt="(dynamic)",
            )

        # Dispatch the merge agent inside the merge worktree.
        self.worker_adapter.working_directory = merge_worktree_path
        merge_instructions = (
            "Merge the following branches into the current branch, resolving conflicts if any.\n\n"
            f"Branches:\n" + "\n".join([f"- {b}" for b in branches]) + "\n\n"
            "Rules:\n"
            "- Use git to merge branches one by one\n"
            "- Resolve conflicts by editing files, then continue the merge\n"
            "- After merging all branches, ensure working tree is clean and committed\n"
        )
        from types import SimpleNamespace

        fake_task = SimpleNamespace(
            description=merge_instructions,
            agent_responsibilities="Integrate parallel stage branches and resolve conflicts.",
            agent_type="integration",
        )
        try:
            response, _metrics = await self.worker_adapter.dispatch(
                task=fake_task, agent=merge_agent, chroma_context=AgentContext(session_summary="", relevant_traces=[], agent_logs=[])
            )
        except WorkerTerminationError as e:
            await git_ops.cleanup_worktree(
                self.working_directory, worktree_path=merge_worktree_path, branch_name=merge_branch_name
            )
            raise DispatchError(
                f"Merge agent could not integrate parallel stage {stage_key}",
                detail=str(e),
            ) from e

        # Ensure merge branch has commits (stage+commit if needed), then merge back to main.
        await git_ops.commit_worktree_changes(worktree_path=merge_worktree_path, task_title=f"merge stage {stage_key}")
        await self._transition(OrchestratorState.MERGING)
        await git_ops.merge_worktree(self.working_directory, branch_name=merge_branch_name, task_title=f"merge stage {stage_key}")
        await git_ops.cleanup_worktree(
            self.working_directory, worktree_path=merge_worktree_path, branch_name=merge_branch_name
        )

    async def _finalize_lingering_worktree(self, task: DbTask) -> None:
        """Merge and remove a worktree left on a task already marked completed (e.g. parallel pre-merge)."""
        if not task.worktree_path or not task.branch_name:
            return
        if not Path(task.worktree_path).exists():
            await self.db_repo.clear_worktree(task.id)
            return
        try:
            await git_ops.merge_worktree(self.working_directory, task.branch_name, task.title)
        except Exception as e:
            logger.warning("Lingering worktree merge skipped | task_id=%s err=%s", task.id, e)
        try:
            await git_ops.cleanup_worktree(
                self.working_directory,
                worktree_path=task.worktree_path,
                branch_name=task.branch_name,
            )
        except Exception as e:
            logger.warning("Lingering worktree cleanup failed | task_id=%s err=%s", task.id, e)
        await self.db_repo.clear_worktree(task.id)

    async def _triage_running_task(self, *, task: DbTask) -> None:
        assert self.current_session is not None
        assignment = await self.db_repo.get_assignment_for_task(task.id)
        if assignment is None:
            await self.db_repo.update_task_status(task.id, TaskStatus.pending)
            await self.db_repo.clear_worktree(task.id)
            return

        agent = await self.db_repo.get_agent(assignment.agent_id)
        worktree_path = task.worktree_path
        branch_name = task.branch_name

        if not worktree_path or not branch_name or not Path(worktree_path).exists():
            await self.db_repo.update_task_status(task.id, TaskStatus.pending)
            await self.db_repo.clear_worktree(task.id)
            await self.db_repo.reopen_assignment(assignment.id)
            return

        try:
            await git_ops.commit_worktree_changes(worktree_path, task.title)
        except Exception as e:
            logger.warning("Triage commit | task_id=%s err=%s", task.id, e)

        has_commits = await git_ops.branch_has_commits(worktree_path, self.working_directory)
        if not has_commits:
            try:
                await git_ops.cleanup_worktree(
                    self.working_directory,
                    worktree_path=worktree_path,
                    branch_name=branch_name,
                )
            except Exception as e:
                logger.warning("Triage cleanup | task_id=%s err=%s", task.id, e)
            await self.db_repo.clear_worktree(task.id)
            await self.db_repo.update_task_status(task.id, TaskStatus.pending)
            await self.db_repo.reopen_assignment(assignment.id)
            return

        created, updated, deleted = await git_ops.get_diff_artifacts_merge_base_to_head(
            worktree_path, self.working_directory
        )
        if not (created or updated or deleted):
            created, updated, deleted = await git_ops.get_changed_files(worktree_path)

        summary = f"[recovered] {task.title}: session interrupted; merged from saved branch"
        response = WorkerResponse(
            type="success",
            summary=summary,
            artifacts=AgentArtifacts(
                created=created,
                updated=updated,
                deleted=deleted,
            ),
            log=MemLog(
                summary="Task completed from git state after session resume.",
                decisions=[],
                assumptions=[],
                open_issues=[],
                room="default",
            ),
        )
        now = datetime.now(timezone.utc)
        metrics = WorkerMetrics(queued_at=now, completed_at=now, latency_ms=0)

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

        trace = MemTrace(
            vault=self.vault_name,
            room="default",
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
        logger.info("Resume triage merge | branch=%s task_title=%s", branch_name, task.title)
        await git_ops.merge_worktree(self.working_directory, branch_name=branch_name, task_title=task.title)
        await git_ops.cleanup_worktree(
            self.working_directory,
            worktree_path=worktree_path,
            branch_name=branch_name,
        )
        await self.db_repo.clear_worktree(task.id)
        await self._transition(OrchestratorState.EXECUTING)

    async def _triage_interrupted_tasks(self, session_id: UUID) -> None:
        plan = await self.db_repo.get_active_plan(session_id)
        if plan is None:
            return
        tasks = await self.db_repo.get_tasks_by_plan(plan.id)
        for task in tasks:
            if task.status == TaskStatus.running:
                await self._triage_running_task(task=task)
        for task in tasks:
            if task.status == TaskStatus.completed and task.worktree_path:
                await self._finalize_lingering_worktree(task)

    async def resume(self, session: DbSession) -> None:
        """
        Continue an interrupted session: reconcile running tasks from git, then run remaining plan.
        Skips lead replanning; restores lead SDK session id for future revise() calls.
        """
        if self._vault is None:
            raise InitializationError("Call initialize_runtime() before resume()")
        if self.worker_adapter is None:
            raise InitializationError("Worker adapter not initialized")

        self.current_session = session
        await self.db_repo.update_session_status(session.id, SessionStatus.active)

        if self.lead_adapter is None:
            self.lead_adapter = LeadAgentAdapter(
                execution_mode=self.execution_mode,
                working_directory=self.working_directory,
                hitl_callback=self._lead_hitl_callback if self.execution_mode == ExecutionMode.HITL else None,
            )
        if session.lead_session_id:
            self.lead_adapter._session_id = session.lead_session_id

        await self._triage_interrupted_tasks(session.id)

        plan = await self.db_repo.get_active_plan(session.id)
        if plan is None or plan.status != PlanStatus.active:
            raise InitializationError("No active execution plan for resumed session")

        await self._transition(OrchestratorState.EXECUTING)
        await self._execute_plan(plan)
        await self._complete_session()
        self.current_session = None

    async def abandon_session(self, session: DbSession) -> None:
        """Discard resumable work: remove worktrees/branches and mark the session aborted."""
        sid = session.id
        try:
            tasks = await self.db_repo.get_tasks_with_worktrees(sid)
            for t in tasks:
                if t.worktree_path and t.branch_name:
                    try:
                        await git_ops.cleanup_worktree(
                            self.working_directory,
                            worktree_path=t.worktree_path,
                            branch_name=t.branch_name,
                        )
                    except Exception as e:
                        logger.warning("abandon worktree cleanup | task_id=%s err=%s", t.id, e)
                await self.db_repo.clear_worktree(t.id)
        except Exception as e:
            logger.warning("abandon worktree pass failed: %s", e)

        plan = await self.db_repo.get_active_plan(sid)
        if plan is not None:
            for t in await self.db_repo.get_tasks_by_plan(plan.id):
                if t.status in (TaskStatus.pending, TaskStatus.running):
                    await self.db_repo.update_task_status(t.id, TaskStatus.cancelled)

        await teardown_session(
            sid,
            status=SessionStatus.aborted,
            orchestrator_state=OrchestratorState.ABORTED,
            db_repo=self.db_repo,
        )

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
                        try:
                            await git_ops.commit_worktree_changes(t.worktree_path, t.title)
                        except Exception as e:
                            logger.warning(
                                "Failed to commit worktree on teardown | task_id=%s err=%s",
                                t.id,
                                e,
                            )
            except Exception as e:
                logger.warning("worktree preservation during teardown: %s", str(e))
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
