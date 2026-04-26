import asyncio

from zeno.agents.lead.adapter import LeadAgentAdapter, LeadAgentContext
from zeno.agents.models import AgentContext, ClarificationAnswer, ClarificationQuestion
from zeno.core.enums import ExecutionMode, LeadAgentStage


async def hitl_callback(
    questions: list[ClarificationQuestion],
) -> list[ClarificationAnswer]:
    # Simulate user answering first option for each question.
    answers: list[ClarificationAnswer] = []
    for q in questions:
        choice = ""
        if q.options:
            choice = q.options[0]
        answers.append(ClarificationAnswer(question_id=q.id, answer=choice))
    return answers


async def main() -> None:
    working_directory = "/tmp/zeno_test"
    context = LeadAgentContext(
        session_id="test-123",
        raw_input="Build a simple weather app",
        mode=ExecutionMode.HITL,
        stage=LeadAgentStage.INITIAL,
        working_directory=working_directory,
        existing_rooms=[],
        agent_context=AgentContext(session_summary="New session", relevant_prior_work=[], agent_history=[]),
        current_plan=None,
        completed_tasks=None,
        revision_reason=None,
    )

    adapter = LeadAgentAdapter(
        execution_mode=ExecutionMode.HITL,
        working_directory=working_directory,
        hitl_callback=hitl_callback,
    )

    plan = await adapter.dispatch(context)
    print(f"Plan received: {plan.task_summary}")
    print(f"Rooms: {[r.name for r in plan.rooms]}")
    print(f"Tasks: {[t.title for t in plan.tasks]}")
    print(f"Session ID: {adapter.session_id}")


if __name__ == "__main__":
    asyncio.run(main())

