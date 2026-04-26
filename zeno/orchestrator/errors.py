from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from rich.console import Console

from zeno.core.enums import OrchestratorState
from zeno.db.models import SessionStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ZenoError(Exception):
    code: str
    message: str
    detail: str | None = None
    recoverable: bool = False

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class InitializationError(ZenoError):
    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(code="INITIALIZATION_ERROR", message=message, detail=detail)


class DispatchError(ZenoError):
    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(code="DISPATCH_ERROR", message=message, detail=detail)


class ParseError(ZenoError):
    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(code="PARSE_ERROR", message=message, detail=detail)


class ValidationError(ZenoError):
    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(code="VALIDATION_ERROR", message=message, detail=detail)


class LeadAgentTerminationError(ZenoError):
    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(code="LEAD_AGENT_TERMINATED", message=message, detail=detail)


class MergeError(ZenoError):
    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(code="MERGE_ERROR", message=message, detail=detail)


class StorageError(ZenoError):
    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(code="STORAGE_ERROR", message=message, detail=detail)


class UnknownError(ZenoError):
    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(code="UNKNOWN_ERROR", message=message, detail=detail)


async def persist_session_failure(
    error: ZenoError,
    session_id: UUID | None,
    db_repo: Any,
) -> None:
    """Mark session and orchestrator state failed (best-effort). Does not exit."""
    if error.detail:
        logger.error("Zeno error detail: %s", error.detail)
    logger.error("Zeno session failure (%s): %s", error.code, error.message)

    if session_id is not None:
        try:
            await db_repo.update_session_status(session_id, SessionStatus.failed)
        except Exception as e:
            logger.error("Failed to set session status=failed: %s", str(e))
        try:
            await db_repo.update_orchestrator_state(session_id, OrchestratorState.FAILED)
        except Exception as e:
            logger.error("Failed to set orchestrator_state=FAILED: %s", str(e))


async def terminate(
    error: ZenoError,
    session_id: UUID | None,
    db_repo: Any,
) -> None:
    """
    Phase 6: fail-fast termination.

    - Log technical detail
    - Mark session FAILED and orchestrator_state FAILED (best-effort)
    - Print a clean user-facing error message
    - Exit non-zero
    """

    await persist_session_failure(error, session_id, db_repo)

    console = Console()
    console.print(f"[red]Error:[/red] {error.message}")
    raise SystemExit(1)

