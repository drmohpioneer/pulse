import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, Field, model_validator

from backend.audio.multimodal import SpeakerRole, SpeakerRoleHypothesis


class DiarizationProviderConfigurationError(RuntimeError):
    """Raised when a configured diarization provider cannot run safely."""


class DiarizationRequest(BaseModel):
    session_id: str
    sequence: int = Field(ge=1)
    audio_reference: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    metadata: Mapping[str, Any] = Field(default_factory=dict)


class DiarizedSpeakerTurn(BaseModel):
    speaker_id: str = "speaker_unknown"
    confidence: float = Field(ge=0.0, le=1.0)
    started_at: datetime
    ended_at: datetime
    is_overlapping: bool = False
    role_hypothesis: SpeakerRoleHypothesis | None = None

    @model_validator(mode="after")
    def validate_time_order(self) -> "DiarizedSpeakerTurn":
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must be after started_at")
        return self


class DiarizationResult(BaseModel):
    provider_name: str
    session_id: str
    sequence: int = Field(ge=1)
    turns: tuple[DiarizedSpeakerTurn, ...] = Field(default_factory=tuple)
    metadata: Mapping[str, Any] = Field(default_factory=dict)


class DiarizationProvider(Protocol):
    provider_name: str

    def diarize(self, request: DiarizationRequest) -> DiarizationResult:
        """Return advisory speaker turns for one audio chunk."""
        ...


class FakeDiarizationProvider:
    """Deterministic diarization provider for tests and demo metadata."""

    provider_name = "fake"

    def diarize(self, request: DiarizationRequest) -> DiarizationResult:
        started_at = request.started_at or datetime.now(UTC)
        ended_at = request.ended_at or started_at + timedelta(
            milliseconds=request.duration_ms or 1000
        )
        speaker_id = str(request.metadata.get("speaker_label") or "speaker_unknown")
        role_hypothesis = role_hypothesis_from_metadata(speaker_id, request.metadata)
        confidence = _bounded_confidence(
            request.metadata.get(
                "diarization_confidence",
                0.5 if speaker_id == "speaker_unknown" else 0.82,
            )
        )
        return DiarizationResult(
            provider_name=self.provider_name,
            session_id=request.session_id,
            sequence=request.sequence,
            turns=(
                DiarizedSpeakerTurn(
                    speaker_id=speaker_id,
                    confidence=confidence,
                    started_at=started_at,
                    ended_at=ended_at,
                    is_overlapping=bool(request.metadata.get("is_overlapping", False)),
                    role_hypothesis=role_hypothesis,
                ),
            ),
            metadata={"deterministic_fake": True},
        )


class RemoteDiarizationProvider:
    provider_name = "remote"

    def __init__(self, *, endpoint: str | None = None) -> None:
        self._endpoint = endpoint or os.getenv("PULSE_DIARIZATION_ENDPOINT")
        if not self._endpoint:
            raise DiarizationProviderConfigurationError(
                "Remote diarization provider requires PULSE_DIARIZATION_ENDPOINT."
            )

    def diarize(self, request: DiarizationRequest) -> DiarizationResult:
        raise DiarizationProviderConfigurationError(
            "Remote diarization adapter is configured but not activated in this slice."
        )


class UnavailableDiarizationProvider:
    provider_name = "unavailable"

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def diarize(self, request: DiarizationRequest) -> DiarizationResult:
        raise DiarizationProviderConfigurationError(self._reason)


def build_diarization_provider(provider_name: str | None = None) -> DiarizationProvider:
    configured_name = (
        provider_name or os.getenv("PULSE_DIARIZATION_PROVIDER") or "fake"
    ).casefold()
    if configured_name == "fake":
        return FakeDiarizationProvider()
    if configured_name == "remote":
        return RemoteDiarizationProvider()
    raise DiarizationProviderConfigurationError(
        f"Unsupported diarization provider: {configured_name}"
    )


def configured_diarization_provider() -> DiarizationProvider:
    try:
        return build_diarization_provider()
    except DiarizationProviderConfigurationError as exc:
        return UnavailableDiarizationProvider(str(exc))


def role_hypothesis_from_metadata(
    speaker_id: str,
    metadata: Mapping[str, Any],
) -> SpeakerRoleHypothesis | None:
    raw_role = metadata.get("speaker_role") or metadata.get("role")
    if raw_role is None:
        return None
    try:
        role = SpeakerRole(str(raw_role))
    except ValueError:
        role = SpeakerRole.UNKNOWN
    confidence = _bounded_confidence(metadata.get("role_confidence", 0.5))
    reason = metadata.get("role_reason") or "metadata_role_hint"
    return SpeakerRoleHypothesis(
        speaker_id=speaker_id,
        role=role,
        confidence=confidence,
        reason=str(reason),
    )


def diarized_turn_id(turn: DiarizedSpeakerTurn) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            f"pulse-diarized-turn:{turn.speaker_id}:{turn.started_at.isoformat()}:{turn.ended_at.isoformat()}",
        )
    )


def _bounded_confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.0
    return max(0.0, min(1.0, confidence))
