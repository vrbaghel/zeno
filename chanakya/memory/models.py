from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MemWing(BaseModel):
    name: str
    path: str  # absolute path to working directory


class MemRoom(BaseModel):
    name: str
    wing: str
    description: str


class MemDiaryEntry(BaseModel):
    summary: str
    decisions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    open_issues: list[str] = Field(default_factory=list)
    room: str

    def to_text(self) -> str:
        parts: list[str] = []
        parts.append(f"Room: {self.room}".strip())
        parts.append("")
        parts.append("Summary:")
        parts.append(self.summary.strip())

        def section(title: str, items: list[str]) -> None:
            if not items:
                return
            parts.append("")
            parts.append(f"{title}:")
            for it in items:
                s = it.strip()
                if s:
                    parts.append(f"- {s}")

        section("Decisions", self.decisions)
        section("Assumptions", self.assumptions)
        section("Dependencies", self.dependencies)
        section("Open issues", self.open_issues)
        return "\n".join(parts).strip() + "\n"


class MemDrawer(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    wing: str
    room: str
    session_id: UUID
    task_id: UUID
    agent_type: str
    created_at: datetime = Field(default_factory=utc_now)
    content: MemDiaryEntry

    def to_document(self) -> str:
        return self.content.to_text()

    def to_metadata(self) -> dict[str, Any]:
        return {
            "wing": self.wing,
            "room": self.room,
            "session_id": str(self.session_id),
            "task_id": str(self.task_id),
            "agent_type": self.agent_type,
            "created_at": self.created_at.isoformat(),
        }


class MemContext(BaseModel):
    session_summary: str
    relevant_drawers: list[MemDrawer] = Field(default_factory=list)
    agent_history: list[MemDrawer] = Field(default_factory=list)

