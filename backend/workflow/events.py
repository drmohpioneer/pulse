from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze_value(item) for item in value)
    return value


def _serialize_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _serialize_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, frozenset | set):
        return [_serialize_value(item) for item in value]
    return value


def _ensure_timezone_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, validate_default=True)


class EventType(StrEnum):
    CPR_STARTED = "cpr_started"
    CPR_PAUSED = "cpr_paused"
    CPR_RESUMED = "cpr_resumed"
    RHYTHM_CHECKED = "rhythm_checked"
    SHOCK_DELIVERED = "shock_delivered"
    MEDICATION_GIVEN = "medication_given"
    AIRWAY_SECURED = "airway_secured"
    REVERSIBLE_CAUSE_CONSIDERED = "reversible_cause_considered"
    ROSC_ACHIEVED = "rosc_achieved"
    UNKNOWN = "unknown"
    TODO = "todo"


class EventSource(StrEnum):
    SPEECH = "speech"
    ACOUSTIC = "acoustic"
    MANUAL = "manual"
    SIMULATED = "simulated"
    DEVICE_FUTURE = "device_future"
    SYSTEM = "system"


class EventStatus(StrEnum):
    CANDIDATE = "candidate"
    ACCEPTED = "accepted"
    NEEDS_CONFIRMATION = "needs_confirmation"
    REJECTED = "rejected"
    CORRECTED = "corrected"
    REVOKED = "revoked"


class Evidence(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    source: EventSource
    evidence_type: str
    timestamp: datetime = Field(default_factory=_utc_now)
    confidence: float = Field(ge=0.0, le=1.0)
    payload: Mapping[str, Any] = Field(default_factory=dict)
    raw_reference: str | None = None
    uncertainty_reason: str | None = None

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _ensure_timezone_aware(value)

    @field_validator("payload", mode="after")
    @classmethod
    def freeze_payload(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return _freeze_value(value)

    @field_serializer("payload")
    def serialize_payload(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return _serialize_value(value)


class CorrectionRecord(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    corrected_at: datetime = Field(default_factory=_utc_now)
    corrected_by: str | None = None
    reason: str | None = None
    previous_status: EventStatus | None = None
    superseded_event_id: UUID

    @field_validator("corrected_at")
    @classmethod
    def validate_corrected_at(cls, value: datetime) -> datetime:
        return _ensure_timezone_aware(value)


class ClinicalEvent(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=_utc_now)
    event_type: EventType
    source: EventSource
    confidence: float = Field(ge=0.0, le=1.0)
    status: EventStatus
    evidence: tuple[Evidence, ...] = Field(min_length=1)
    supersedes_event_id: UUID | None = None
    correction_history: tuple[CorrectionRecord, ...] = Field(default_factory=tuple)
    payload: Mapping[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)

    @field_validator("timestamp", "created_at")
    @classmethod
    def validate_datetime(cls, value: datetime) -> datetime:
        return _ensure_timezone_aware(value)

    @field_validator("payload", mode="after")
    @classmethod
    def freeze_payload(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return _freeze_value(value)

    @field_serializer("payload")
    def serialize_payload(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return _serialize_value(value)

    @model_validator(mode="after")
    def validate_correction_reference(self) -> "ClinicalEvent":
        if self.status == EventStatus.CORRECTED and self.supersedes_event_id is None:
            raise ValueError("corrected events must reference the event they supersede")
        return self

    @property
    def supersedes_another_event(self) -> bool:
        return self.supersedes_event_id is not None
