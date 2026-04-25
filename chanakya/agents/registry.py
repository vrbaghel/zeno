from __future__ import annotations

from dataclasses import dataclass

from chanakya.agents.base import BaseAgentAdapter
from chanakya.agents.worker.gemini import GeminiAdaptor


@dataclass(frozen=True)
class AdaptorRegistry:
    _adaptors: dict[str, BaseAgentAdapter]
    _available: set[str]

    @classmethod
    def discover(cls) -> "AdaptorRegistry":
        # Phase 2: known adaptors are static (Gemini only).
        adaptors: list[BaseAgentAdapter] = [GeminiAdaptor()]
        by_name: dict[str, BaseAgentAdapter] = {}
        available: set[str] = set()

        for a in adaptors:
            info = a.adaptor_info()
            name = str(info.get("name") or a.__class__.__name__).lower()
            by_name[name] = a
            if a.probe():
                available.add(name)

        return cls(_adaptors=by_name, _available=available)

    def available(self) -> list[str]:
        return sorted(self._available)

    def get(self, name: str) -> BaseAgentAdapter:
        key = name.strip().lower()
        if key not in self._adaptors:
            raise KeyError(f"Adaptor not found: {name}")
        return self._adaptors[key]

    def default(self) -> BaseAgentAdapter:
        for name in self.available():
            return self.get(name)
        # If nothing is available, still return the first known adaptor
        # (useful for introspection; dispatch will return ADAPTOR_NOT_FOUND).
        return next(iter(self._adaptors.values()))

