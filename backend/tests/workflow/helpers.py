from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from backend.workflow.events import (
    ClinicalEvent,
    EventSource,
    EventStatus,
    EventType,
    Evidence,
)

BASE_TIME = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


def at(minutes: int = 0, seconds: int = 0) -> datetime:
    return BASE_TIME + timedelta(minutes=minutes, seconds=seconds)


def make_event(
    event_type: EventType,
    *,
    minutes: int = 0,
    seconds: int = 0,
    rhythm: str | None = None,
    medication: str | None = None,
    dose: float | None = None,
    unit: str | None = None,
    route: str | None = None,
    cause: str | None = None,
    cause_confidence: float | None = None,
    suggested_intervention: str | None = None,
    reversible_cause_evidence: list[str] | None = None,
    status: EventStatus = EventStatus.ACCEPTED,
    supersedes_event_id: UUID | None = None,
) -> ClinicalEvent:
    payload: dict[str, Any] = {}
    if rhythm is not None:
        payload["rhythm"] = rhythm
    if medication is not None:
        payload["medication"] = medication
    if dose is not None:
        payload["dose"] = dose
    if unit is not None:
        payload["unit"] = unit
    if route is not None:
        payload["route"] = route
    if cause is not None:
        payload["cause"] = cause
    if cause_confidence is not None:
        payload["confidence"] = cause_confidence
    if suggested_intervention is not None:
        payload["suggested_intervention"] = suggested_intervention
    if reversible_cause_evidence is not None:
        payload["evidence"] = reversible_cause_evidence

    return ClinicalEvent(
        event_type=event_type,
        source=EventSource.SIMULATED,
        confidence=0.95,
        status=status,
        evidence=(
            Evidence(
                source=EventSource.SIMULATED,
                evidence_type="test",
                confidence=0.95,
                payload=payload,
            ),
        ),
        payload=payload,
        timestamp=at(minutes=minutes, seconds=seconds),
        supersedes_event_id=supersedes_event_id,
    )
