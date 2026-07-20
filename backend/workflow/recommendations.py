from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field


class RecommendationPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Recommendation(BaseModel):
    id: str
    priority: RecommendationPriority
    message: str
    rationale: str | None = None
    referenced_state_fields: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False


class RecommendationProvider(Protocol):
    def next_actions(self) -> list[Recommendation]:
        """Return deterministic next-action placeholders."""
        # TODO: Implement only after ACLS workflow architecture is accepted.
        ...

