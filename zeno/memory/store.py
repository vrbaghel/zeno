from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import chromadb

from zeno.memory.models import MemLog, MemTrace

logger = logging.getLogger(__name__)


def _persist_dir(working_directory: str) -> Path:
    wd = Path(working_directory).resolve()
    return wd / ".zeno" / "mind" / "chroma"


def _collection_name(vault: str) -> str:
    return f"zeno_{vault}"


def _client(working_directory: str) -> chromadb.PersistentClient:
    d = _persist_dir(working_directory)
    d.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(d))


def _collection(working_directory: str, *, vault: str):
    c = _client(working_directory)
    return c.get_or_create_collection(name=_collection_name(vault))


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


def _trace_from_row(
    *,
    trace_id: str,
    document: str | None,
    metadata: dict[str, Any] | None,
) -> MemTrace:
    md = metadata or {}
    created_at = _parse_dt(md.get("created_at"))

    # We only store log text; reconstruct a minimal shell for now.
    # (We still keep `room` aligned with metadata so retrieval can display it.)
    log = MemLog(
        summary=(document or "").strip() or "(no log content)",
        decisions=[],
        assumptions=[],
        dependencies=[],
        open_issues=[],
        room=str(md.get("room") or ""),
    )

    return MemTrace(
        id=UUID(trace_id),
        vault=str(md.get("vault") or ""),
        room=str(md.get("room") or ""),
        session_id=UUID(str(md.get("session_id"))),
        task_id=UUID(str(md.get("task_id"))),
        agent_type=str(md.get("agent_type") or ""),
        agent_id=str(md.get("agent_id") or ""),
        created_at=created_at or datetime.now(timezone.utc),
        content=log,
    )


def save_trace(working_directory: str, trace: MemTrace, agent_id: str) -> None:
    logger.debug(
        "Saving trace | vault=%s room=%s agent_type=%s task_id=%s",
        trace.vault,
        trace.room,
        trace.agent_type,
        trace.task_id,
    )
    col = _collection(working_directory, vault=trace.vault)
    md = trace.to_metadata()
    md["agent_id"] = agent_id
    col.add(
        ids=[str(trace.id)],
        documents=[trace.to_document()],
        metadatas=[md],
    )


def get_traces(
    working_directory: str, vault: str, room: str | None, limit: int = 10
) -> list[MemTrace]:
    col = _collection(working_directory, vault=vault)
    where: dict[str, Any] = {"vault": vault}
    if room:
        where["room"] = room
    res = col.get(where=_where_eq(where), limit=limit, include=["documents", "metadatas"])
    ids = res.get("ids") or []
    docs = res.get("documents") or []
    mds = res.get("metadatas") or []
    out: list[MemTrace] = []
    for i, did in enumerate(ids):
        out.append(
            _trace_from_row(
                trace_id=did,
                document=docs[i] if i < len(docs) else None,
                metadata=mds[i] if i < len(mds) else None,
            )
        )
    return out


def search_traces(
    working_directory: str,
    query: str,
    vault: str,
    room: str | None = None,
    limit: int = 5,
) -> list[MemTrace]:
    logger.debug("Searching traces | vault=%s room=%s limit=%d query=%r", vault, room, limit, query[:50])
    col = _collection(working_directory, vault=vault)
    where: dict[str, Any] = {"vault": vault}
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
    out: list[MemTrace] = []
    for i, did in enumerate(ids):
        out.append(
            _trace_from_row(
                trace_id=did,
                document=docs[i] if i < len(docs) else None,
                metadata=mds[i] if i < len(mds) else None,
            )
        )
    logger.debug("Trace search results | count=%d", len(out))
    if query.strip() and not out:
        logger.warning("ChromaDB search returned no results | vault=%s query=%r", vault, query[:50])
    return out


def get_agent_logs(
    working_directory: str,
    vault: str,
    agent_type: str | None = None,
    agent_id: str | None = None,
    room: str | None = None,
    limit: int = 3,
) -> list[MemTrace]:
    col = _collection(working_directory, vault=vault)
    where: dict[str, Any] = {"vault": vault}
    if agent_type is not None:
        where["agent_type"] = agent_type
    if agent_id is not None:
        where["agent_id"] = agent_id
    if room is not None:
        where["room"] = room
    res = col.get(
        where=_where_eq(where),
        limit=limit,
        include=["documents", "metadatas"],
    )
    ids = res.get("ids") or []
    docs = res.get("documents") or []
    mds = res.get("metadatas") or []
    out: list[MemTrace] = []
    for i, did in enumerate(ids):
        out.append(
            _trace_from_row(
                trace_id=did,
                document=docs[i] if i < len(docs) else None,
                metadata=mds[i] if i < len(mds) else None,
            )
        )
    return out

