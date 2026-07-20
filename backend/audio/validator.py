from typing import Protocol

from pydantic import BaseModel

from backend.audio.detector import AudioObservation


class AudioValidationResult(BaseModel):
    observation_id: str
    is_valid: bool | None = None
    reason: str | None = None


class AudioObservationValidator(Protocol):
    def validate(self, observation: AudioObservation) -> AudioValidationResult:
        """Validate audio observations before conversion to evidence."""
        # TODO: Define audio validation policy and test fixtures.
        ...

