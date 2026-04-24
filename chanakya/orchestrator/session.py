from __future__ import annotations

import logging
from pathlib import Path

from chanakya.core.enums import OrchestratorState
from chanakya.db.models import DbSession, SessionMode, SessionStatus
from chanakya.memory import palace
from chanakya.orchestrator.errors import InitializationError, StorageError
from chanakya.orchestrator.git import ensure_git_initialized

logger = logging.getLogger(__name__)


async def initialize_session(
    raw_input: str,
    execution_mode: SessionMode,
    working_directory: str,
    *,
    db_repo,
) -> tuple[DbSession, str]:
    """
    Returns (DbSession, wing_name).
    """
    try:
        root = Path(working_directory).resolve()
        (root / ".chanakya").mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise InitializationError(
            "Failed to initialize .chanakya directory",
            detail=str(e),
        ) from e

    await ensure_git_initialized(str(Path(working_directory).resolve()))

    try:
        wing = await palace.initialize_wing(working_directory)
    except Exception as e:
        raise InitializationError("Failed to initialize ChromaDB palace", detail=str(e)) from e

    try:
        existing = await db_repo.get_wing_by_path(str(Path(working_directory).resolve()))
        if existing is None:
            await db_repo.create_wing(name=wing.name, path=wing.path)
    except Exception as e:
        raise StorageError("Failed to initialize SQLite wing record", detail=str(e)) from e

    try:
        session = await db_repo.create_session(
            mode=execution_mode,
            working_directory=str(Path(working_directory).resolve()),
            raw_input=raw_input,
        )
    except Exception as e:
        raise StorageError("Failed to create session in SQLite", detail=str(e)) from e

    try:
        await db_repo.update_orchestrator_state(session.id, OrchestratorState.INITIALIZING)
        await db_repo.update_orchestrator_state(session.id, OrchestratorState.AWAITING_LEAD)
    except Exception as e:
        raise StorageError("Failed to update orchestrator state in SQLite", detail=str(e)) from e

    return session, wing.name


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

