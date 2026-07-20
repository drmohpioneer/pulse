from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, Field


class AudioSegment(BaseModel):
    id: str
    started_at: datetime
    ended_at: datetime | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class AudioObservation(BaseModel):
    id: str
    segment_id: str
    observation_type: str
    confidence: float
    metadata: dict[str, str] = Field(default_factory=dict)


class VoiceActivityDetector(Protocol):
    def detect(self, segment: AudioSegment) -> list[AudioObservation]:
        """Detect speech candidate segments."""
        # TODO: Add provider-specific implementation outside workflow engine.
        ...


class AudioEventDetector(Protocol):
    def detect(self, segment: AudioSegment) -> list[AudioObservation]:
        """Detect non-speech clinical acoustic observations."""
        # TODO: Add defibrillator/monitor acoustic detectors after validation plan.
        ...

