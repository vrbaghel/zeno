from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomllib
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_CONFIG_PATH = Path.home() / ".zeno" / "config.toml"


class ZenoSettings(BaseSettings):
    """
    Phase 1 settings:
    - orchestrator_timeout_seconds: optional
    """

    model_config = SettingsConfigDict(extra="ignore")

    orchestrator_timeout_seconds: float = Field(
        default=120.0,
        validation_alias=AliasChoices("ORCHESTRATOR_TIMEOUT_SECONDS", "ZENO_ORCHESTRATOR_TIMEOUT_SECONDS"),
    )


@dataclass(frozen=True)
class LoadedConfig:
    path: Path
    found: bool
    settings: ZenoSettings


def _read_toml_file(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        data = tomllib.load(f)
    if not isinstance(data, dict):
        return {}
    return data


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> LoadedConfig:
    """
    Load ~/.zeno/config.toml if present.

    If missing, returns defaults and found=False.
    Env vars can still populate settings via Pydantic.
    """

    if not path.exists():
        return LoadedConfig(path=path, found=False, settings=ZenoSettings())

    data = _read_toml_file(path)
    return LoadedConfig(path=path, found=True, settings=ZenoSettings(**data))

