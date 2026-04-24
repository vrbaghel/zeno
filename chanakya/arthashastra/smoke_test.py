"""
Run: `python -m chanakya.arthashastra.smoke_test`

Validates Phase 5 lead-agent contracts and validation rules.
"""

from __future__ import annotations

from uuid import uuid4

from chanakya.arthashastra.models import (
    ClarificationResponse,
    DiaryEntry,
    ExecutionPlanResponse,
    RoomDefinition,
    TaskDefinition,
    validate_lead_response,
)


def main() -> None:
    clar = ClarificationResponse(
        context="Need to clarify requirements.",
        questions=[],
    )
    assert validate_lead_response(clar, mode="hitl") == []
    assert validate_lead_response(clar, mode="yolo") != []

    plan = ExecutionPlanResponse(
        task_summary="Do the thing.",
        rooms=[RoomDefinition(name="frontend", description="UI work")],
        tasks=[
            TaskDefinition(
                id="task-1",
                title="Implement UI",
                description="Build the UI.",
                type="implementation",
                agent_type="coding",
                room="frontend",
                depends_on=[],
                parallel_group=None,
                checkpoint_before=False,
            )
        ],
        assumptions=["Assume React is used."],
        diary_entry=DiaryEntry(
            summary="Decomposed the work into tasks and rooms.",
            decisions=[],
            assumptions=[],
            dependencies=[],
            open_issues=[],
            room="frontend",
        ),
    )
    assert validate_lead_response(plan, mode="hitl") == []

    # invalid dependency reference
    bad = plan.model_copy(deep=True)
    bad.tasks[0].depends_on = ["task-does-not-exist"]
    assert validate_lead_response(bad, mode="hitl") != []

    # invalid parallel group
    bad2 = plan.model_copy(deep=True)
    bad2.tasks[0].parallel_group = "aa"
    assert validate_lead_response(bad2, mode="hitl") != []

    print("arthashastra_smoke_test: OK", uuid4())


if __name__ == "__main__":
    main()

