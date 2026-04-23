from __future__ import annotations

from rich.console import Console

from chanakya.arthashastra.models import AdaptorError, AdaptorMetrics, AdaptorResponse


def _fmt_list(items: list[str]) -> str:
    if not items:
        return "(none)"
    if len(items) == 1:
        return items[0]
    return ", ".join(items)


def print_adaptor_result(
    console: Console,
    *,
    response: AdaptorResponse | None = None,
    metrics: AdaptorMetrics | None = None,
    error: AdaptorError | None = None,
) -> None:
    if error is not None:
        console.print("── Error " + "─" * 35)
        console.print(f"  Code       : {error.code}")
        console.print(f"  Message    : {error.message}")
        console.print(f"  Recoverable: {'yes' if error.recoverable else 'no'}")
        if error.request_id:
            console.print(f"  Request ID : {error.request_id}")
        if error.agent_id:
            console.print(f"  Agent ID   : {error.agent_id}")
        return

    assert response is not None
    assert metrics is not None

    console.print("── AdaptorResponse " + "─" * 26)
    console.print(f"  Status      : {response.status.value}")
    console.print(f"  Agent ID    : {response.agent_id}")
    console.print(f"  Request ID  : {response.request_id}")
    console.print(f"  Session ID  : {response.session_id}")
    console.print(f"  Created At  : {response.created_at}")
    console.print()

    console.print("  Payload")
    for m in response.payload.messages:
        console.print(f"    [{m.role}]: {m.content}")
    if not response.payload.messages:
        console.print("    (none)")
    console.print()

    console.print("  Artifacts")
    console.print(f"    Created  : {_fmt_list(response.artifacts.created)}")
    console.print(f"    Updated  : {_fmt_list(response.artifacts.updated)}")
    console.print(f"    Deleted  : {_fmt_list(response.artifacts.deleted)}")
    console.print()

    console.print("── AdaptorMetrics " + "─" * 27)
    console.print(f"  Provider    : {metrics.provider}")
    console.print(f"  Mode        : {metrics.mode}")
    console.print(f"  Model       : {metrics.model or '(as reported)'}")
    console.print()

    console.print("  Timing")
    console.print(f"    Latency             : {metrics.timing.latency_ms} ms")
    console.print(f"    Time to first token : {metrics.timing.time_to_first_token_ms} ms")
    console.print()

    # With the unified tiktoken strategy, we treat this as an estimate with a per-provider deviation label.
    console.print("  Tokens")
    console.print(f"    Input      : {metrics.tokens.input}")
    console.print(f"    Output     : {metrics.tokens.output}")
    console.print(f"    Total      : {metrics.tokens.total}")
    console.print("    Estimated  : yes")
    console.print("    Method     : tiktoken_cl100k")
    console.print(f"    Deviation  : {metrics.tokens.deviation or '(unknown)'}")
    console.print()

    console.print("── Error " + "─" * 35)
    console.print("  (none)")

