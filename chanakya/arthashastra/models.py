from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


AdaptorRole = Literal["system", "user", "assistant", "tool"]


class AdaptorMessage(BaseModel):
    role: AdaptorRole
    content: str


class AdaptorArtifacts(BaseModel):
    created: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    deleted: list[str] = Field(default_factory=list)


class AdaptorRequestPayload(BaseModel):
    system: str | None = None
    messages: list[AdaptorMessage] = Field(default_factory=list)
    tools: list[dict] = Field(default_factory=list)


class AdaptorRequestConfig(BaseModel):
    max_tokens: int | None = None
    temperature: float | None = None
    timeout_seconds: float | None = 60.0


class AdaptorRequest(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    session_id: UUID = Field(default_factory=uuid4)
    agent_id: str
    created_at: datetime = Field(default_factory=utc_now)
    payload: AdaptorRequestPayload = Field(default_factory=AdaptorRequestPayload)
    config: AdaptorRequestConfig = Field(default_factory=AdaptorRequestConfig)


class AdaptorResponseStatus(str, Enum):
    success = "success"
    error = "error"
    timeout = "timeout"
    truncated = "truncated"


class AdaptorResponsePayload(BaseModel):
    messages: list[AdaptorMessage] = Field(default_factory=list)


class AdaptorResponse(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    request_id: UUID
    session_id: UUID
    agent_id: str
    status: AdaptorResponseStatus
    created_at: datetime = Field(default_factory=utc_now)
    payload: AdaptorResponsePayload = Field(default_factory=AdaptorResponsePayload)
    artifacts: AdaptorArtifacts = Field(default_factory=AdaptorArtifacts)


class AdaptorErrorCode(str, Enum):
    ADAPTOR_NOT_FOUND = "ADAPTOR_NOT_FOUND"
    ADAPTOR_SPAWN_FAILED = "ADAPTOR_SPAWN_FAILED"
    ADAPTOR_TIMEOUT = "ADAPTOR_TIMEOUT"
    ADAPTOR_PARSE_ERROR = "ADAPTOR_PARSE_ERROR"
    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"
    RESPONSE_TRUNCATED = "RESPONSE_TRUNCATED"
    UNKNOWN = "UNKNOWN"


class AdaptorError(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    request_id: UUID | None = None
    agent_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    code: AdaptorErrorCode
    message: str
    recoverable: bool = False


class AdaptorTimingMetrics(BaseModel):
    queued_at: datetime | None = None
    dispatched_at: datetime | None = None
    first_token_at: datetime | None = None
    completed_at: datetime | None = None
    latency_ms: int | None = None
    time_to_first_token_ms: int | None = None


class TokenEstimationMethod(str, Enum):
    exact = "exact"
    approximate = "approximate"
    unavailable = "unavailable"


class AdaptorTokenMetrics(BaseModel):
    input: int | None = None
    output: int | None = None
    total: int | None = None
    estimated: bool = True
    estimation_method: TokenEstimationMethod = TokenEstimationMethod.unavailable


class AdaptorArtifactMetrics(BaseModel):
    created_count: int = 0
    updated_count: int = 0
    deleted_count: int = 0


class AdaptorMetrics(BaseModel):
    timing: AdaptorTimingMetrics = Field(default_factory=AdaptorTimingMetrics)
    tokens: AdaptorTokenMetrics = Field(default_factory=AdaptorTokenMetrics)
    artifacts: AdaptorArtifactMetrics = Field(default_factory=AdaptorArtifactMetrics)
    agent_id: str
    mode: Literal["adapter", "api"]
    provider: Literal["gemini", "anthropic", "openai"]
    model: str | None = None

