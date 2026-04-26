import asyncio
import os
from dataclasses import dataclass

from zeno.agents.models import AgentContext
from zeno.agents.worker.adapter import WorkerAdapter


@dataclass(frozen=True)
class _Task:
    title: str
    description: str
    agent_type: str
    agent_responsibilities: str


@dataclass(frozen=True)
class _Agent:
    name: str
    type: str


async def main() -> None:
    wd = "/tmp/zeno_worker_test"
    os.makedirs(wd, exist_ok=True)

    task = _Task(
        title="Create a hello world Python script",
        description="Create a Python script called hello.py that prints Hello from Zeno",
        agent_type="coding",
        agent_responsibilities="Write clean Python code and report all files created",
    )

    agent = _Agent(name="coding-agent", type="coding")

    chroma_context = AgentContext(
        session_summary="Test session — standalone worker test",
        relevant_prior_work=[],
        agent_history=[],
    )

    adapter = WorkerAdapter(working_directory=wd)

    response, metrics = await adapter.dispatch(task=task, agent=agent, chroma_context=chroma_context)

    print(f"Summary: {response.summary}")
    print(f"Created: {response.artifacts.created}")
    print(f"Updated: {response.artifacts.updated}")
    print(f"Deleted: {response.artifacts.deleted}")
    print(f"Log summary: {response.log.summary}")
    print(f"Tokens: {metrics.total_tokens}")
    print(f"Cost: {metrics.cost_usd}")
    print(f"Latency: {metrics.latency_ms}ms")

    print("\\nFiles on disk:")
    for f in sorted(os.listdir(wd)):
        print(f"  - {f}")


if __name__ == "__main__":
    asyncio.run(main())

