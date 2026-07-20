import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from pydantic import BaseModel, Field, model_validator

from backend.audio.multimodal import AcousticObservation, AcousticObservationType


class AcousticEventProviderConfigurationError(RuntimeError):
    """Raised when a configured acoustic event provider cannot run safely."""


class AcousticEventRequest(BaseModel):
    session_id: str
    sequence: int = Field(ge=1)
    audio_reference: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    metadata: Mapping[str, Any] = Field(default_factory=dict)


class AcousticEventDetection(BaseModel):
    acoustic_type: AcousticObservationType
    confidence: float = Field(ge=0.0, le=1.0)
    started_at: datetime
    ended_at: datetime
    source_metadata: Mapping[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_time_order(self) -> "AcousticEventDetection":
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must be after started_at")
        return self


class AcousticEventResult(BaseModel):
    provider_name: str
    session_id: str
    sequence: int = Field(ge=1)
    detections: tuple[AcousticEventDetection, ...] = Field(default_factory=tuple)
    metadata: Mapping[str, Any] = Field(default_factory=dict)


class AcousticEventProvider(Protocol):
    provider_name: str

    def detect(self, request: AcousticEventRequest) -> AcousticEventResult:
        """Return advisory acoustic detections for one audio chunk."""
        ...


class FakeAcousticEventProvider:
    """Deterministic acoustic detector for tests and demo metadata."""

    provider_name = "fake"

    def detect(self, request: AcousticEventRequest) -> AcousticEventResult:
        raw_type = request.metadata.get("acoustic_observation_type")
        if raw_type is None:
            return AcousticEventResult(
                provider_name=self.provider_name,
                session_id=request.session_id,
                sequence=request.sequence,
                metadata={"deterministic_fake": True},
            )

        started_at = request.started_at or datetime.now(UTC)
        ended_at = request.ended_at or started_at + timedelta(
            milliseconds=request.duration_ms or 1000
        )
        observation_type = AcousticObservationType(str(raw_type))
        detection = AcousticEventDetection(
            acoustic_type=observation_type,
            confidence=_bounded_confidence(request.metadata.get("acoustic_confidence", 0.86)),
            started_at=started_at,
            ended_at=ended_at,
            source_metadata={
                "provider": self.provider_name,
                "audio_reference": request.audio_reference,
                "deterministic_fake": True,
            },
        )
        return AcousticEventResult(
            provider_name=self.provider_name,
            session_id=request.session_id,
            sequence=request.sequence,
            detections=(detection,),
            metadata={"deterministic_fake": True},
        )


class RemoteAcousticEventProvider:
    provider_name = "remote"

    def __init__(self, *, endpoint: str | None = None) -> None:
        self._endpoint = endpoint or os.getenv("PULSE_ACOUSTIC_ENDPOINT")
        if not self._endpoint:
            raise AcousticEventProviderConfigurationError(
                "Remote acoustic provider requires PULSE_ACOUSTIC_ENDPOINT."
            )

    def detect(self, request: AcousticEventRequest) -> AcousticEventResult:
        raise AcousticEventProviderConfigurationError(
            "Remote acoustic adapter is configured but not activated in this slice."
        )


class UnavailableAcousticEventProvider:
    provider_name = "unavailable"

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def detect(self, request: AcousticEventRequest) -> AcousticEventResult:
        raise AcousticEventProviderConfigurationError(self._reason)


def build_acoustic_event_provider(
    provider_name: str | None = None,
) -> AcousticEventProvider:
    configured_name = (
        provider_name or os.getenv("PULSE_ACOUSTIC_PROVIDER") or "fake"
    ).casefold()
    if configured_name == "fake":
        return FakeAcousticEventProvider()
    if configured_name == "remote":
        return RemoteAcousticEventProvider()
    raise AcousticEventProviderConfigurationError(
        f"Unsupported acoustic provider: {configured_name}"
    )


def configured_acoustic_event_provider() -> AcousticEventProvider:
    try:
        return build_acoustic_event_provider()
    except AcousticEventProviderConfigurationError as exc:
        return UnavailableAcousticEventProvider(str(exc))


def detection_to_acoustic_observation(
    *,
    chunk_id: str,
    detection: AcousticEventDetection,
) -> AcousticObservation:
    return AcousticObservation(
        chunk_id=chunk_id,
        observation_type=detection.acoustic_type,
        confidence=detection.confidence,
        started_at=detection.started_at,
        ended_at=detection.ended_at,
        raw_reference=detection.acoustic_type.value,
        metadata=dict(detection.source_metadata),
    )


def _bounded_confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.0
    return max(0.0, min(1.0, confidence))
