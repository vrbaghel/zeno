from __future__ import annotations

from enum import Enum


class ExecutionMode(str, Enum):
    """How Chanakya interacts with the user during a session (YOLO vs HITL)."""

    YOLO = "yolo"
    HITL = "hitl"


class OrchestratorState(str, Enum):
    INITIALIZING = "INITIALIZING"
    AWAITING_LEAD = "AWAITING_LEAD"
    AWAITING_HUMAN = "AWAITING_HUMAN"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    MERGING = "MERGING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class LeadAgentStage(str, Enum):
    INITIAL = "initial"
    REVISION = "revision"

