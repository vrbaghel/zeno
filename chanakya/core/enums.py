from __future__ import annotations

from enum import Enum


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

