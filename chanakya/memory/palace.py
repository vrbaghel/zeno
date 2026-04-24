from __future__ import annotations

import re
from pathlib import Path

import chromadb

from chanakya.db.repository import (
    create_room as db_create_room,
    create_wing as db_create_wing,
    get_room_by_name,
    get_rooms as db_get_rooms,
    get_wing_by_path,
    room_exists as db_room_exists,
)
from chanakya.memory.models import MemRoom, MemWing


def _slugify(s: str) -> str:
    s2 = s.strip().lower()
    s2 = re.sub(r"[^a-z0-9]+", "-", s2)
    s2 = re.sub(r"-{2,}", "-", s2).strip("-")
    return s2 or "project"


def _ensure_chanakya_dir(working_directory: str) -> Path:
    root = Path(working_directory).resolve()
    d = root / ".chanakya"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _chroma_persist_dir(working_directory: str) -> Path:
    return _ensure_chanakya_dir(working_directory) / "memory" / "chroma"


def _collection_name(wing_name: str) -> str:
    return f"chanakya_{wing_name}"


def _get_client(working_directory: str) -> chromadb.PersistentClient:
    persist_dir = _chroma_persist_dir(working_directory)
    persist_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(persist_dir))


def _ensure_collection(working_directory: str, *, wing_name: str) -> None:
    client = _get_client(working_directory)
    client.get_or_create_collection(name=_collection_name(wing_name))


async def initialize_wing(working_directory: str) -> MemWing:
    wd = str(Path(working_directory).resolve())
    wing_name = _slugify(Path(wd).name)

    existing = await get_wing_by_path(wd)
    if existing is None:
        await db_create_wing(name=wing_name, path=wd)

    _ensure_collection(wd, wing_name=wing_name)
    return MemWing(name=wing_name, path=wd)


async def check_palace(working_directory: str) -> bool:
    wd = str(Path(working_directory).resolve())
    try:
        wing_name = _slugify(Path(wd).name)
        _ensure_collection(wd, wing_name=wing_name)
        return True
    except Exception:
        try:
            await initialize_wing(wd)
            return True
        except Exception:
            return False


async def create_room(wing: MemWing, name: str, description: str) -> MemRoom:
    w = await get_wing_by_path(wing.path)
    if w is None:
        w = await db_create_wing(name=wing.name, path=wing.path)

    r = await db_create_room(wing_id=w.id, name=name, description=description)
    return MemRoom(name=r.name, wing=wing.name, description=r.description)


async def get_rooms(wing: MemWing) -> list[MemRoom]:
    w = await get_wing_by_path(wing.path)
    if w is None:
        return []
    rooms = await db_get_rooms(w.id)
    return [MemRoom(name=r.name, wing=wing.name, description=r.description) for r in rooms]


async def room_exists(wing: MemWing, name: str) -> bool:
    w = await get_wing_by_path(wing.path)
    if w is None:
        return False
    return await db_room_exists(w.id, name)


async def get_room_description(wing: MemWing, name: str) -> str | None:
    w = await get_wing_by_path(wing.path)
    if w is None:
        return None
    r = await get_room_by_name(w.id, name)
    return None if r is None else r.description

