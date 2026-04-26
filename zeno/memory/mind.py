from __future__ import annotations

import re
from pathlib import Path

import chromadb

from zeno.db.repository import (
    create_room as db_create_room,
    create_vault as db_create_vault,
    get_room_by_name,
    get_rooms as db_get_rooms,
    get_vault_by_path,
    room_exists as db_room_exists,
)
from zeno.memory.models import MemRoom, MemVault


def _slugify(s: str) -> str:
    s2 = s.strip().lower()
    s2 = re.sub(r"[^a-z0-9]+", "-", s2)
    s2 = re.sub(r"-{2,}", "-", s2).strip("-")
    return s2 or "project"


def _ensure_zeno_dir(working_directory: str) -> Path:
    root = Path(working_directory).resolve()
    d = root / ".zeno"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _chroma_persist_dir(working_directory: str) -> Path:
    return _ensure_zeno_dir(working_directory) / "mind" / "chroma"


def _collection_name(vault_name: str) -> str:
    return f"zeno_{vault_name}"


def _get_client(working_directory: str) -> chromadb.PersistentClient:
    persist_dir = _chroma_persist_dir(working_directory)
    persist_dir.mkdir(parents=True, exist_ok=True)
    try:
        return chromadb.PersistentClient(path=str(persist_dir))
    except Exception as e:
        # Surface the actual Chroma failure + the path we attempted.
        raise RuntimeError(
            f"ChromaDB PersistentClient init failed (path={persist_dir})"
        ) from e


def _ensure_collection(working_directory: str, *, vault_name: str) -> None:
    client = _get_client(working_directory)
    client.get_or_create_collection(name=_collection_name(vault_name))


async def initialize_vault(working_directory: str) -> MemVault:
    wd = str(Path(working_directory).resolve())
    vault_name = _slugify(Path(wd).name)

    existing = await get_vault_by_path(wd)
    if existing is None:
        await db_create_vault(name=vault_name, path=wd)

    _ensure_collection(wd, vault_name=vault_name)
    return MemVault(name=vault_name, path=wd)


async def check_mind(working_directory: str) -> bool:
    wd = str(Path(working_directory).resolve())
    try:
        vault_name = _slugify(Path(wd).name)
        _ensure_collection(wd, vault_name=vault_name)
        return True
    except Exception:
        try:
            await initialize_vault(wd)
            return True
        except Exception:
            return False


async def create_room(vault: MemVault, name: str, description: str) -> MemRoom:
    v = await get_vault_by_path(vault.path)
    if v is None:
        v = await db_create_vault(name=vault.name, path=vault.path)

    r = await db_create_room(vault_id=v.id, name=name, description=description)
    return MemRoom(name=r.name, vault=vault.name, description=r.description)


async def get_rooms(vault: MemVault) -> list[MemRoom]:
    v = await get_vault_by_path(vault.path)
    if v is None:
        return []
    rooms = await db_get_rooms(v.id)
    return [MemRoom(name=r.name, vault=vault.name, description=r.description) for r in rooms]


async def room_exists(vault: MemVault, name: str) -> bool:
    v = await get_vault_by_path(vault.path)
    if v is None:
        return False
    return await db_room_exists(v.id, name)


async def get_room_description(vault: MemVault, name: str) -> str | None:
    v = await get_vault_by_path(vault.path)
    if v is None:
        return None
    r = await get_room_by_name(v.id, name)
    return None if r is None else r.description

