from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from rich.console import Console

from zeno.agents.lead.adapter import LeadAgentAdapter, LeadAgentContext
from zeno.agents.lead.composer import compose_prompt
from zeno.agents.models import (
    AgentContext,
    CheckpointContent,
    CheckpointOption,
    ClarificationInput,
    LeadAgentResponse,
)
from zeno.agents.registry import AdaptorRegistry
from zeno.cli import display as cli_display
from zeno.core.config import load_config
from zeno.core.enums import ExecutionMode, LeadAgentStage, OrchestratorState
from zeno.core.mode import OperationMode
from zeno.db import repository as db_repository
from zeno.db.engine import dispose_db_engine
from zeno.db.models import (
    AgentMode,
    AgentType,
    DbSession,
    DbTask,
    Provider,
    SessionStatus,
    TaskType,
)
from zeno.memory.models import MemVault
from zeno.memory.mind import initialize_vault as initialize_mem_vault
from zeno.memory.retrieval import build_context
from zeno.memory.store import save_trace
from zeno.orchestrator import git as git_ops
from zeno.orchestrator.dispatch import dispatch_agent
from zeno.orchestrator.errors import (
    ZenoError,
    InitializationError,
    LeadAgentTerminationError,
    StorageError,
    UnknownError,
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
        self.available_providers: list[str] = []

        self.current_session: DbSession | None = None
        self._vault: MemVault | None = None

        self.registry = AdaptorRegistry.discover()
        self.db_repo = db_repository
        self._sm = _StateMachine.phase6()

        self._rich_console = Console()

    async def initialize_runtime(self) -> None:
        if self.operation_mode == OperationMode.adapter and not self.registry.available():
            raise InitializationError("No adaptors available (is `gemini` on PATH?)")
        vault_name, _wd = await prepare_workspace(self.working_directory, db_repo=self.db_repo)
        self._vault = await initialize_mem_vault(self.working_directory)
        if self._vault.name != vault_name:
            logger.warning("vault name mismatch: %s vs %s", self._vault.name, vault_name)

    @property
    def vault_name(self) -> str:
        if self._vault is None:
            raise InitializationError("Orchestrator runtime not initialized")
        return self._vault.name

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
        if self._vault is None:
            raise InitializationError("Call initialize_runtime() before run()")

        t0 = perf_counter()
        self.current_session = None
        metrics = None
        success = False
        try:
            session, providers = await initialize_session(
                raw_input=raw_input,
                execution_mode=self.execution_mode,
                operation_mode=self.operation_mode,
                working_directory=self.working_directory,
                db_repo=self.db_repo,
            )
            self.current_session = session
            self.available_providers = providers

            st = await self.db_repo.get_orchestrator_state(session.id)
            cli_display.print_state_transition(self._rich_console, st)

            # Lead agent planning
            lead_plan = await self._dispatch_lead_agent(
                stage=LeadAgentStage.INITIAL,
                raw_input=raw_input,
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
            plan = await planner.build_plan(
                lead_plan,
                session=session,
                available_providers=self.available_providers,
            )

            # HITL plan approval loop
            if self.execution_mode == ExecutionMode.HITL and self.hitl_callback is not None:
                while True:
                    action = await self._create_checkpoint(
                        CheckpointContent(
                            type="plan_approval",
                            title="Approve execution plan?",
                            description="Approve to begin execution, revise to modify the plan, or cancel to abort.",
                            options=[],
                        )
                    )
                    if action == "approve":
                        break
                    if action == "cancel":
                        await teardown_session(
                            session.id,
                            status=SessionStatus.aborted,
                            orchestrator_state=OrchestratorState.ABORTED,
                            db_repo=self.db_repo,
                        )
                        return False
                    if action == "revise":
                        lead_plan = await self._dispatch_lead_agent(
                            stage=LeadAgentStage.REVISION,
                            raw_input=raw_input,
                            current_plan=lead_plan,
                            completed_tasks=[],
                            revision_reason="User requested revision.",
                        )
                        plan = await planner.build_plan(
                            lead_plan,
                            session=session,
                            available_providers=self.available_providers,
                        )
                        continue
                    # Unknown choice: loop.

            await self._transition(OrchestratorState.EXECUTING)

            runnable = await self.db_repo.get_runnable_tasks(plan.id)
            if not runnable:
                raise UnknownError("No runnable tasks produced by plan", detail=plan.id.hex)
            task = runnable[0]

            assignment = await self.db_repo.get_assignment_for_task(task.id)
            if assignment is None:
                raise StorageError("No assignment found for planned task", detail=str(task.id))

            agent = await self.db_repo.get_agent(assignment.agent_id)

            await self._hitl_checkpoint_for_tasks([task])

            cli_display.print_task_activity(
                self._rich_console,
                task_title=task.title,
                agent_type=str(agent.type),
                status="running",
            )

            worktree_path, branch_name = await git_ops.create_worktree(
                self.working_directory, session.id, task.id
            )
            await self.db_repo.assign_worktree(task.id, worktree_path=worktree_path, branch_name=branch_name)

            loaded = load_config()
            timeout_s = float(getattr(loaded.settings, "orchestrator_timeout_seconds", 120.0))
            response, metrics_out, trace = await dispatch_agent(
                task=task,
                agent=agent,
                assignment=assignment,
                session=session,
                vault=self._vault,
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
            save_trace(self.working_directory, trace, agent_id=str(agent.id))
            await self.db_repo.complete_assignment(assignment.id)
            await self.db_repo.complete_task(task.id, result_summary="completed")

            cli_display.print_task_activity(
                self._rich_console,
                task_title=task.title,
                agent_type=str(agent.type),
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

    async def _dispatch_lead_agent(
        self,
        *,
        stage: LeadAgentStage,
        raw_input: str,
        current_plan: LeadAgentResponse | None,
        completed_tasks: list[str] | None,
        revision_reason: str | None,
    ) -> LeadAgentResponse:
        if self._vault is None:
            raise InitializationError("Orchestrator runtime not initialized")
        if self.current_session is None:
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
            current_tasks = []
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

        agent_context = _to_agent_context(mem_ctx)

        ctx = LeadAgentContext(
            session_id=str(self.current_session.id),
            raw_input=raw_input,
            mode=self.execution_mode,
            stage=stage,
            working_directory=wd,
            existing_rooms=existing_rooms,
            agent_context=agent_context,
            available_providers=self.available_providers,
            current_plan=current_plan,  # type: ignore[arg-type]
            completed_tasks=completed_tasks,
            revision_reason=revision_reason,
        )

        prompt = compose_prompt(mode=self.execution_mode, stage=stage, context=ctx)
        loaded = load_config()
        timeout_s = float(getattr(loaded.settings, "orchestrator_timeout_seconds", 120.0))
        lead = LeadAgentAdapter(timeout_seconds=timeout_s)
        await lead.start(prompt)

        while True:
            resp = await lead.read_response()
            if resp.type == "clarification":
                if self.execution_mode == ExecutionMode.YOLO:
                    await lead.terminate()
                    raise ValidationError("Lead agent produced clarification in YOLO mode")
                if self.hitl_callback is None:
                    await lead.terminate()
                    raise ValidationError("Clarification requested but no HITL callback is configured")

                assert resp.options is not None
                content = CheckpointContent(
                    type="unexpected",
                    title=resp.question or "Clarification needed",
                    description=(resp.context or "").strip() or "Choose one option.",
                    options=[
                        CheckpointOption(key="a", label=resp.options.option_a.label),
                        CheckpointOption(key="b", label=resp.options.option_b.label),
                        CheckpointOption(key="c", label=resp.options.option_c.label),
                    ],
                    payload={},
                )
                choice = await self._create_checkpoint(content)
                label = {
                    "a": resp.options.option_a.label,
                    "b": resp.options.option_b.label,
                    "c": resp.options.option_c.label,
                }.get(choice, resp.options.option_a.label)
                ans = ClarificationInput(
                    question=resp.question or "",
                    choice=choice if choice in {"a", "b", "c"} else "a",
                    label=label,
                )
                await lead.send_answers(ans)
                continue

            if resp.type == "terminate":
                await lead.terminate()
                raise LeadAgentTerminationError(resp.reason or "Lead agent terminated")

            if resp.type == "execution":
                await lead.terminate()
                return resp


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


def _to_agent_context(mem_ctx) -> AgentContext:
    if mem_ctx is None:
        return AgentContext(session_summary="", relevant_prior_work=[], agent_history=[])

    relevant: list[str] = []
    try:
        for d in getattr(mem_ctx, "relevant_traces", []) or []:
            txt = d.to_document().strip()
            if txt:
                relevant.append(txt)
    except Exception:
        relevant = []

    history: list[str] = []
    try:
        for d in getattr(mem_ctx, "agent_logs", []) or []:
            txt = d.to_document().strip()
            if txt:
                history.append(txt)
    except Exception:
        history = []

    return AgentContext(
        session_summary=str(getattr(mem_ctx, "session_summary", "") or ""),
        relevant_prior_work=relevant,
        agent_history=history,
    )
