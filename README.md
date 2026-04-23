# Chanakya

Chanakya is a multi-agent orchestration framework.

Phase 1 implements a single CLI command, `chanakya`, that bootstraps configuration, resolves runtime mode, validates the mode, prints a startup summary, and exits.

## Phase 2 (Arthashastra adapters)

Phase 2 adds the Arthashastra adaptor contract and a first implementation: the Gemini CLI adaptor. There is still **no CLI wiring** (nothing calls adaptors from `chanakya` yet).

Minimal (manual) usage:

```python
import asyncio

from chanakya.arthashastra.models import AdaptorMessage, AdaptorRequest, AdaptorRequestPayload
from chanakya.arthashastra.registry import AdaptorRegistry


async def main():
    registry = AdaptorRegistry.discover()
    adaptor = registry.default()

    req = AdaptorRequest(
        agent_id="demo-agent",
        payload=AdaptorRequestPayload(
            system="You are a helpful assistant.",
            messages=[AdaptorMessage(role="user", content="Say hello.")],
        ),
    )

    result = await adaptor.dispatch(req)
    print(result)


asyncio.run(main())
```


