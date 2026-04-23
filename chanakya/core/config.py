from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomllib
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_CONFIG_PATH = Path.home() / ".chanakya" / "config.toml"


class ChanakyaSettings(BaseSettings):
    """
    Phase 1 settings:
    - mode: optional
    - API keys: optional (required only if mode resolves to api)
    """

    model_config = SettingsConfigDict(extra="ignore")

    mode: str | None = None

    openai_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    anthropic_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    gemini_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    )


@dataclass(frozen=True)
class LoadedConfig:
    path: Path
    found: bool
    settings: ChanakyaSettings


def _read_toml_file(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        data = tomllib.load(f)
    if not isinstance(data, dict):
        return {}
    return data


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> LoadedConfig:
    """
    Load ~/.chanakya/config.toml if present.

    If missing, returns defaults and found=False.
    Env vars can still populate settings via Pydantic.
    """

    if not path.exists():
        return LoadedConfig(path=path, found=False, settings=ChanakyaSettings())

    data = _read_toml_file(path)
    return LoadedConfig(path=path, found=True, settings=ChanakyaSettings(**data))

