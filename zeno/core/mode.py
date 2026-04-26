from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from shutil import which

from zeno.core.config import ZenoSettings


class OperationMode(str, Enum):
    adapter = "adapter"
    api = "api"

    @classmethod
    def parse(cls, value: str | None) -> "OperationMode | None":
        if value is None:
            return None
        normalized = value.strip().lower()
        try:
            return cls(normalized)
        except ValueError:
            return None


ModeSource = str  # "--mode" | "config" | "default"


def resolve_mode(
    *, cli_mode: OperationMode | None, config_mode: OperationMode | None
) -> tuple[OperationMode, ModeSource]:
    if cli_mode is not None:
        return cli_mode, "--mode"
    if config_mode is not None:
        return config_mode, "config"
    return OperationMode.adapter, "default"


@dataclass(frozen=True)
class AdapterStatus:
    name: str
    found: bool
    path: str | None = None


def scan_adapters(names: list[str] | None = None) -> list[AdapterStatus]:
    names = names or ["claude", "gemini", "codex"]
    statuses: list[AdapterStatus] = []
    for n in names:
        p = which(n)
        statuses.append(AdapterStatus(name=n, found=p is not None, path=p))
    return statuses


@dataclass(frozen=True)
class ApiKeyStatus:
    name: str
    env_var: str
    set: bool


def api_key_statuses(settings: ZenoSettings) -> list[ApiKeyStatus]:
    statuses: list[ApiKeyStatus] = []
    statuses.append(
        ApiKeyStatus(name="OpenAI key", env_var="OPENAI_API_KEY", set=bool(settings.openai_key))
    )
    statuses.append(
        ApiKeyStatus(
            name="Anthropic key", env_var="ANTHROPIC_API_KEY", set=bool(settings.anthropic_key)
        )
    )
    statuses.append(
        ApiKeyStatus(name="Gemini key", env_var="GEMINI_API_KEY", set=bool(settings.gemini_key))
    )
    return statuses


def api_mode_has_any_key(settings: ZenoSettings) -> bool:
    return any(s.set for s in api_key_statuses(settings))

