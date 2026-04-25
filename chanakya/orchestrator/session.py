from __future__ import annotations

import logging
from pathlib import Path

from chanakya.core.enums import ExecutionMode, OrchestratorState
from chanakya.core.mode import OperationMode
from chanakya.db.models import DbSession, SessionStatus
from chanakya.memory import palace
from chanakya.agents.registry import AdaptorRegistry
from chanakya.orchestrator.errors import InitializationError, StorageError
from chanakya.orchestrator.git import ensure_git_initialized

logger = logging.getLogger(__name__)


async def prepare_workspace(working_directory: str, *, db_repo) -> tuple[str, str]:
    """
    Ensure .chanakya, git, Chroma palace, and SQLite wing exist. Does not create a session.
    Returns (wing_name, resolved_working_directory).
    """
    try:
        wd = str(Path(working_directory).resolve())
        root = Path(wd)
        (root / ".chanakya").mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise InitializationError(
            "Failed to initialize .chanakya directory",
            detail=str(e),
        ) from e

    await ensure_git_initialized(wd)

    try:
        wing = await palace.initialize_wing(wd)
    except Exception as e:
        raise InitializationError("Failed to initialize ChromaDB palace", detail=str(e)) from e

    try:
        existing = await db_repo.get_wing_by_path(wd)
        if existing is None:
            await db_repo.create_wing(name=wing.name, path=wing.path)
    except Exception as e:
        raise StorageError("Failed to initialize SQLite wing record", detail=str(e)) from e

    return wing.name, wd


async def initialize_session(
    raw_input: str,
    execution_mode: ExecutionMode,
    operation_mode: OperationMode,
    working_directory: str,
    *,
    db_repo,
) -> tuple[DbSession, list[str]]:
    """
    Returns (DbSession, available_providers).
    """
    _wing_name, wd = await prepare_workspace(working_directory, db_repo=db_repo)

    try:
        session = await db_repo.create_session(
            mode=execution_mode,
            working_directory=wd,
            raw_input=raw_input,
        )
    except Exception as e:
        raise StorageError("Failed to create session in SQLite", detail=str(e)) from e

    # Phase 8B: Providers available for this run.
    # Today, we only support adaptor-based mode; api mode is not implemented.
    # Still probe registry so the lead agent can assign providers correctly.
    if operation_mode != OperationMode.adapter:
        logger.warning("operation_mode=%s is not implemented; probing adaptor registry anyway", operation_mode)
    registry = AdaptorRegistry.discover()
    providers = registry.available()

    try:
        await db_repo.update_orchestrator_state(session.id, OrchestratorState.INITIALIZING)
        await db_repo.update_orchestrator_state(session.id, OrchestratorState.AWAITING_LEAD)
    except Exception as e:
        raise StorageError("Failed to update orchestrator state in SQLite", detail=str(e)) from e

    return session, providers


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

