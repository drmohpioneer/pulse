from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ConfidenceBand(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CONFLICTING = "conflicting"
    UNKNOWN = "unknown"


class Explanation(BaseModel):
    summary: str = Field(..., description="Human-readable explanation placeholder.")
    referenced_event_ids: list[str] = Field(default_factory=list)
    referenced_state_fields: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceMetadata(BaseModel):
    source_id: str | None = None
    source_detail: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

