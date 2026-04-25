from __future__ import annotations

import logging
from uuid import UUID, uuid4

from chanakya.agents.models import (
    AdaptorError,
    AdaptorMessage,
    AdaptorMetrics,
    AdaptorRequest,
    AdaptorRequestPayload,
    AdaptorResponse,
    AdaptorResponseStatus,
)
from chanakya.agents.registry import AdaptorRegistry
from chanakya.core.mode import OperationMode
from chanakya.db.models import DbAgent, DbAgentAssignment, DbSession, DbTask
from chanakya.memory.models import MemContext, MemDiaryEntry, MemDrawer, MemWing
from chanakya.memory.retrieval import build_context
from chanakya.orchestrator.errors import DispatchError, ParseError, StorageError, ValidationError

logger = logging.getLogger(__name__)


def _render_mem_context(ctx: MemContext) -> str:
    relevant = "\n\n".join([d.to_document().strip() for d in ctx.relevant_drawers if d.to_document()])
    history = "\n\n".join([d.to_document().strip() for d in ctx.agent_history if d.to_document()])

    parts: list[str] = []
    parts.append("## Memory context")
    parts.append("")
    parts.append("### Session summary")
    parts.append(ctx.session_summary.strip())

    if relevant.strip():
        parts.append("")
        parts.append("### Relevant prior work")
        parts.append(relevant.strip())

    if history.strip():
        parts.append("")
        parts.append("### Agent history")
        parts.append(history.strip())

    return "\n".join(parts).strip() + "\n"


def _drawer_from_adaptor_response(
    *,
    response: AdaptorResponse,
    wing: MemWing,
    session_id: UUID,
    task_id: UUID,
    agent_type: str,
    agent_id: str,
) -> MemDrawer:
    room = "default"
    summary = ""

    # If the adaptor returned structured content via Gemini's AgentResponse, it will have
    # been converted into AdaptorResponse payload + artifacts (diary_entry isn't part of
    # AdaptorResponse today). We store a minimal diary summary.
    if response.payload.messages:
        summary = response.payload.messages[-1].content.strip()
    if not summary:
        summary = "(no response content)"

    diary = MemDiaryEntry(
        summary=summary[:4000],
        decisions=[],
        assumptions=[],
        dependencies=[],
        open_issues=[],
        room=room,
    )
    return MemDrawer(
        wing=wing.name,
        room=room,
        session_id=session_id,
        task_id=task_id,
        agent_type=agent_type,
        agent_id=agent_id,
        content=diary,
    )


async def dispatch_agent(
    *,
    task: DbTask,
    agent: DbAgent,
    assignment: DbAgentAssignment,
    session: DbSession,
    wing: MemWing,
    db_repo,
    operation_mode: OperationMode,
    timeout_seconds: float,
) -> tuple[AdaptorResponse, AdaptorMetrics, MemDrawer]:
    try:
        # Repository currently exposes plan-scoped task retrieval.
        current_tasks = await db_repo.get_tasks_by_plan(task.plan_id)
    except Exception as e:
        raise StorageError("Failed to load current session tasks", detail=str(e)) from e

    mem_ctx = build_context(
        working_directory=session.working_directory,
        wing=wing,
        task_description=task.description,
        agent_type=str(agent.type),
        agent_id=str(agent.id),
        session_id=session.id,
        current_session_tasks=current_tasks,
    )

    user_content = f"{task.description.strip()}\n\n{_render_mem_context(mem_ctx)}"

    request = AdaptorRequest(
        id=uuid4(),
        session_id=session.id,
        agent_id=str(agent.id),
        timeout_seconds=timeout_seconds,
        payload=AdaptorRequestPayload(
            system=agent.system_prompt,
            messages=[AdaptorMessage(role="user", content=user_content)],
            tools=[],
        ),
    )

    # Phase 6: operation_mode only affects *how* we talk to the LLM; today we only have
    # adaptor-based dispatch via Gemini CLI, so we dispatch via registry.default().
    if operation_mode != OperationMode.adapter:
        logger.warning("operation_mode=%s is not implemented; falling back to adaptor", operation_mode)

    try:
        await db_repo.start_assignment(assignment.id)
    except Exception as e:
        raise StorageError("Failed to mark assignment running", detail=str(e)) from e

    registry = AdaptorRegistry.discover()
    adaptor = registry.get(str(agent.provider))
    result = await adaptor.dispatch(request)

    if isinstance(result, AdaptorError):
        raise DispatchError(result.message, detail=result.model_dump_json())  # noqa: TRY003

    response, metrics = result

    # Parse/validate: ensure we have some assistant content and a success-ish status.
    if response.status in (AdaptorResponseStatus.error, AdaptorResponseStatus.timeout):
        raise DispatchError(
            "Adaptor returned an error status",
            detail=response.model_dump_json(),
        )

    if not response.payload.messages:
        raise ValidationError(
            "Agent response missing messages",
            detail=response.model_dump_json(),
        )

    try:
        drawer = _drawer_from_adaptor_response(
            response=response,
            wing=wing,
            session_id=session.id,
            task_id=task.id,
            agent_type=str(agent.type),
            agent_id=str(agent.id),
        )
    except Exception as e:
        raise ParseError("Failed to build memory drawer from response", detail=str(e)) from e

    return response, metrics, drawer

