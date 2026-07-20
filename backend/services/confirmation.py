from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ConfirmationOption(StrEnum):
    CONFIRM = "confirm"
    REJECT = "reject"
    CORRECT = "correct"


class ConfirmationRequest(BaseModel):
    id: str
    candidate_event_id: str
    reason: str
    confidence: float
    options: list[ConfirmationOption] = Field(default_factory=list)
    expires_at: datetime | None = None


class ConfirmationResolution(BaseModel):
    request_id: str
    selected_option: ConfirmationOption
    resolved_by: str | None = None
    corrected_event_id: str | None = None

