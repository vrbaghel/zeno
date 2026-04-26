from __future__ import annotations

import logging
from pathlib import Path

from zeno.core.enums import ExecutionMode, OrchestratorState
from zeno.db.engine import create_all_tables
from zeno.db.models import DbSession, SessionStatus
from zeno.memory import mind
from zeno.orchestrator.errors import InitializationError, StorageError
from zeno.orchestrator.git import ensure_git_initialized, ensure_initial_commit

logger = logging.getLogger(__name__)


async def prepare_workspace(working_directory: str, *, db_repo) -> tuple[str, str]:
    """
    Ensure .zeno, git, mind storage, and SQLite vault exist. Does not create a session.
    Returns (vault_name, resolved_working_directory).
    """
    try:
        wd = str(Path(working_directory).resolve())
        root = Path(wd)
        (root / ".zeno").mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise InitializationError(
            "Failed to initialize .zeno directory",
            detail=str(e),
        ) from e

    await ensure_git_initialized(wd)
    await ensure_initial_commit(wd)

    try:
        # Ensure SQLite schema exists before mind init touches vault/room tables.
        await create_all_tables()
    except Exception as e:
        raise InitializationError(
            "Failed to initialize SQLite schema",
            detail=f"{type(e).__name__}: {e}",
        ) from e

    try:
        vault = await mind.initialize_vault(wd)
    except Exception as e:
        raise InitializationError(
            "Failed to initialize mind storage",
            detail=f"{type(e).__name__}: {e}",
        ) from e

    try:
        existing = await db_repo.get_vault_by_path(wd)
        if existing is None:
            await db_repo.create_vault(name=vault.name, path=vault.path)
    except Exception as e:
        raise StorageError("Failed to initialize SQLite vault record", detail=str(e)) from e

    return vault.name, wd


async def initialize_session(
    raw_input: str,
    execution_mode: ExecutionMode,
    working_directory: str,
    *,
    db_repo,
) -> DbSession:
    _vault_name, wd = await prepare_workspace(working_directory, db_repo=db_repo)

    try:
        session = await db_repo.create_session(
            mode=execution_mode,
            working_directory=wd,
            raw_input=raw_input,
        )
    except Exception as e:
        raise StorageError("Failed to create session in SQLite", detail=str(e)) from e

    try:
        await db_repo.update_orchestrator_state(session.id, OrchestratorState.INITIALIZING)
        await db_repo.update_orchestrator_state(session.id, OrchestratorState.AWAITING_LEAD)
    except Exception as e:
        raise StorageError("Failed to update orchestrator state in SQLite", detail=str(e)) from e

    return session


async def teardown_session(
    session_id,
    *,
    status: SessionStatus,
    orchestrator_state: OrchestratorState,
    db_repo,
) -> None:
    try:
        await db_repo.update_session_status(session_id, status)
    except Exception as e:
        logger.error("Failed to update session status: %s", str(e))
    try:
        await db_repo.update_orchestrator_state(session_id, orchestrator_state)
    except Exception as e:
        logger.error("Failed to update orchestrator state: %s", str(e))

