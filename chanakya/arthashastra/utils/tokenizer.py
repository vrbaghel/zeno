from __future__ import annotations

import tiktoken


def count(text: str) -> int:
    """
    Unified token counting strategy.

    Uses tiktoken with cl100k_base for all providers.
    """

    enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(text))

