from __future__ import annotations

import asyncio
import os
import sys

from chanakya.core.enums import ExecutionMode
from chanakya.core.mode import OperationMode
from chanakya.orchestrator.core import OrchestratorCore


async def main() -> None:
    working_directory = os.environ.get("CHANAKYA_SMOKE_WD") or os.getcwd()

    orchestrator = OrchestratorCore(
        execution_mode=ExecutionMode.YOLO,
        operation_mode=OperationMode.adapter,
        working_directory=working_directory,
        hitl_callback=None,
    )
    await orchestrator.initialize_runtime()
    ok = await orchestrator.run(
        "Create a file called hello.txt with content Hello Chanakya",
    )
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
