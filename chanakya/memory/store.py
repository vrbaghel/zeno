from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import chromadb

from chanakya.memory.models import MemDiaryEntry, MemDrawer


def _persist_dir(working_directory: str) -> Path:
    wd = Path(working_directory).resolve()
    return wd / ".chanakya" / "memory" / "chroma"


def _collection_name(wing: str) -> str:
    return f"chanakya_{wing}"


def _client(working_directory: str) -> chromadb.PersistentClient:
    d = _persist_dir(working_directory)
    d.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(d))


def _collection(working_directory: str, *, wing: str):
    c = _client(working_directory)
    return c.get_or_create_collection(name=_collection_name(wing))


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _where_eq(filters: dict[str, Any]) -> dict[str, Any] | None:
    if not filters:
        return None
    if len(filters) == 1:
        k, v = next(iter(filters.items()))
        return {k: {"$eq": v}}
    return {"$and": [{k: {"$eq": v}} for k, v in filters.items()]}


def _drawer_from_row(
    *,
    drawer_id: str,
    document: str | None,
    metadata: dict[str, Any] | None,
) -> MemDrawer:
    md = metadata or {}
    created_at = _parse_dt(md.get("created_at"))

    # We only store diary text; reconstruct a minimal diary entry shell for now.
    # (We still keep `room` aligned with metadata so retrieval can display it.)
    diary = MemDiaryEntry(
        summary=(document or "").strip() or "(no diary content)",
        decisions=[],
        assumptions=[],
        dependencies=[],
        open_issues=[],
        room=str(md.get("room") or ""),
    )

    return MemDrawer(
        id=UUID(drawer_id),
        wing=str(md.get("wing") or ""),
        room=str(md.get("room") or ""),
        session_id=UUID(str(md.get("session_id"))),
        task_id=UUID(str(md.get("task_id"))),
        agent_type=str(md.get("agent_type") or ""),
        created_at=created_at or datetime.now(timezone.utc),
        content=diary,
    )


def save_drawer(working_directory: str, drawer: MemDrawer) -> None:
    col = _collection(working_directory, wing=drawer.wing)
    col.add(
        ids=[str(drawer.id)],
        documents=[drawer.to_document()],
        metadatas=[drawer.to_metadata()],
    )


def get_drawers(working_directory: str, wing: str, room: str | None, limit: int = 10) -> list[MemDrawer]:
    col = _collection(working_directory, wing=wing)
    where: dict[str, Any] = {"wing": wing}
    if room:
        where["room"] = room
    res = col.get(where=_where_eq(where), limit=limit, include=["documents", "metadatas"])
    ids = res.get("ids") or []
    docs = res.get("documents") or []
    mds = res.get("metadatas") or []
    out: list[MemDrawer] = []
    for i, did in enumerate(ids):
        out.append(_drawer_from_row(drawer_id=did, document=docs[i] if i < len(docs) else None, metadata=mds[i] if i < len(mds) else None))
    return out


def search_drawers(
    working_directory: str,
    query: str,
    wing: str,
    room: str | None = None,
    limit: int = 5,
) -> list[MemDrawer]:
    col = _collection(working_directory, wing=wing)
    where: dict[str, Any] = {"wing": wing}
    if room:
        where["room"] = room
    res = col.query(
        query_texts=[query],
        n_results=limit,
        where=_where_eq(where),
        include=["documents", "metadatas"],
    )
    ids = (res.get("ids") or [[]])[0]
    docs = (res.get("documents") or [[]])[0]
    mds = (res.get("metadatas") or [[]])[0]
    out: list[MemDrawer] = []
    for i, did in enumerate(ids):
        out.append(_drawer_from_row(drawer_id=did, document=docs[i] if i < len(docs) else None, metadata=mds[i] if i < len(mds) else None))
    return out


def get_agent_history(
    working_directory: str, wing: str, agent_type: str, limit: int = 3
) -> list[MemDrawer]:
    col = _collection(working_directory, wing=wing)
    res = col.get(
        where=_where_eq({"wing": wing, "agent_type": agent_type}),
        limit=limit,
        include=["documents", "metadatas"],
    )
    ids = res.get("ids") or []
    docs = res.get("documents") or []
    mds = res.get("metadatas") or []
    out: list[MemDrawer] = []
    for i, did in enumerate(ids):
        out.append(_drawer_from_row(drawer_id=did, document=docs[i] if i < len(docs) else None, metadata=mds[i] if i < len(mds) else None))
    return out

