from __future__ import annotations

import asyncio
import os

from chanakya.core.mode import OperationMode
from chanakya.db.models import SessionMode
from chanakya.orchestrator.core import OrchestratorCore


async def main() -> None:
    working_directory = os.environ.get("CHANKYA_SMOKE_WD") or os.getcwd()

    orchestrator = OrchestratorCore(
        raw_input="Create a file called hello.txt with content Hello Chanakya",
        execution_mode=SessionMode.yolo,
        operation_mode=OperationMode.adapter,
        working_directory=working_directory,
    )
    await orchestrator.run()


if __name__ == "__main__":
    asyncio.run(main())

