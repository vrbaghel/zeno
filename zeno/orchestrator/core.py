from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from zeno.agents.models import CheckpointContent
from zeno.cli import display as cli_display
from zeno.core.enums import ExecutionMode, OrchestratorState
from zeno.db import repository as db_repository
from zeno.db.engine import dispose_db_engine
from zeno.db.models import DbSession, SessionStatus
from zeno.memory.models import MemVault
from zeno.memory.mind import initialize_vault as initialize_mem_vault
from zeno.orchestrator import git as git_ops
from zeno.orchestrator.errors import (
    DispatchError,
    InitializationError,
    UnknownError,
    ZenoError,
    persist_session_failure,
)
from zeno.orchestrator.session import initialize_session, prepare_workspace, teardown_session

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
        working_directory: str,
        hitl_callback: Callable[[CheckpointContent], Awaitable[str]] | None = None,
    ) -> None:
        self.execution_mode = execution_mode
        self.working_directory = str(Path(working_directory).resolve())
        self.hitl_callback = hitl_callback

        self.current_session: DbSession | None = None
        self._vault: MemVault | None = None

        self.db_repo = db_repository
        self._sm = _StateMachine.phase6()
        self._rich_console = Console()

    async def initialize_runtime(self) -> None:
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

    async def run(self, raw_input: str) -> bool:
        if self._vault is None:
            raise InitializationError("Call initialize_runtime() before run()")

        self.current_session = None
        success = False
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

            raise DispatchError(
                "Agent dispatch is not implemented yet (expected in Migration 2).",
                detail="Migration 1 removes adaptor/subprocess dispatch; SDK execution comes later.",
            )

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
