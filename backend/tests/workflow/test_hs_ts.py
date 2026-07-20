from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from backend.workflow.events import (
    ClinicalEvent,
    EventSource,
    EventStatus,
    EventType,
    Evidence,
)
from backend.workflow.hs_ts import ReversibleCauseStateMachine
from backend.workflow.recommendations import RecommendationPriority

BASE_TIME = datetime(2026, 7, 19, 9, 0, tzinfo=UTC)


def _event(
    *,
    cause: str | None = "Hypoxia",
    suggested_intervention: str | None = "optimize oxygenation",
    confidence: float | str | None = 0.82,
    evidence: list[str] | None = None,
    status: EventStatus = EventStatus.ACCEPTED,
    event_type: EventType = EventType.REVERSIBLE_CAUSE_CONSIDERED,
    minutes: int = 0,
    supersedes_event_id=None,
) -> ClinicalEvent:
    payload: dict[str, object] = {}
    if cause is not None:
        payload["cause"] = cause
    if suggested_intervention is not None:
        payload["suggested_intervention"] = suggested_intervention
    if confidence is not None:
        payload["confidence"] = confidence
    if evidence is not None:
        payload["evidence"] = evidence

    return ClinicalEvent(
        event_type=event_type,
        source=EventSource.SIMULATED,
        confidence=0.5,
        status=status,
        evidence=(
            Evidence(
                source=EventSource.SIMULATED,
                evidence_type="test",
                confidence=0.5,
                payload=payload,
            ),
        ),
        payload=payload,
        timestamp=BASE_TIME + timedelta(minutes=minutes),
        supersedes_event_id=supersedes_event_id,
    )


def test_accepted_reversible_cause_event_updates_public_state() -> None:
    machine = ReversibleCauseStateMachine()
    event = _event(evidence=["etco2-low", "cyanosis-observed"])

    state = machine.apply_event(event)

    assert state.possible_cause == "Hypoxia"
    assert state.confidence == 0.82
    assert state.evidence_ids == ("etco2-low", "cyanosis-observed")
    assert state.suggested_intervention == "optimize oxygenation"
    assert len(state.causes) == 1
    assert state.causes[0].event_ids == (str(event.id),)


def test_unaccepted_and_unrelated_events_are_ignored() -> None:
    machine = ReversibleCauseStateMachine()

    machine.apply_event(_event(status=EventStatus.CANDIDATE))
    machine.apply_event(_event(event_type=EventType.RHYTHM_CHECKED))
    machine.apply_event(_event(cause=None))

    assert machine.get_state().possible_cause is None
    assert machine.get_recommendations() == []


def test_replay_skips_superseded_reversible_cause_events() -> None:
    first = _event(
        cause="Hypoxia",
        suggested_intervention="optimize oxygenation",
        confidence=0.9,
        evidence=["old-evidence"],
    )
    correction = _event(
        cause="Hypovolemia",
        suggested_intervention="give fluids",
        confidence=0.7,
        evidence=["new-evidence"],
        status=EventStatus.CORRECTED,
        minutes=1,
        supersedes_event_id=first.id,
    )
    machine = ReversibleCauseStateMachine()

    state = machine.replay([first, correction])

    assert state.possible_cause == "Hypovolemia"
    assert state.confidence == 0.7
    assert state.evidence_ids == ("new-evidence",)
    assert [cause.name for cause in state.causes] == ["Hypovolemia"]


def test_recommendations_are_deterministic_and_pure() -> None:
    machine = ReversibleCauseStateMachine()
    machine.apply_event(
        _event(
            cause="Tension pneumothorax",
            suggested_intervention="decompress the chest",
            confidence=0.81,
            evidence=["absent-breath-sounds"],
        )
    )
    before = machine.get_state()

    first = machine.get_recommendations()
    second = machine.get_recommendations()
    after = machine.get_state()

    assert first == second
    assert after == before
    assert first[0].id == "hs_ts.suggested_intervention.tension_pneumothorax"
    assert first[0].priority == RecommendationPriority.HIGH
    assert first[0].message == "Consider decompress the chest."
    assert first[0].requires_confirmation is True


def test_multiple_causes_rank_by_confidence_then_name() -> None:
    machine = ReversibleCauseStateMachine()
    machine.apply_event(
        _event(
            cause="Tamponade",
            suggested_intervention="consider pericardiocentesis",
            confidence=0.7,
            evidence=["ultrasound-effusion"],
        )
    )
    machine.apply_event(
        _event(
            cause="Tension pneumothorax",
            suggested_intervention="decompress the chest",
            confidence=0.9,
            evidence=["unilateral-breath-sounds"],
            minutes=1,
        )
    )

    state = machine.get_state()
    recommendations = machine.get_recommendations()

    assert state.possible_cause == "Tension pneumothorax"
    assert recommendations[0].id == "hs_ts.suggested_intervention.tension_pneumothorax"
    assert recommendations[1].id == "hs_ts.suggested_intervention.tamponade"


def test_state_snapshots_are_immutable_and_defensive() -> None:
    machine = ReversibleCauseStateMachine()
    machine.apply_event(_event(evidence=["pulse-ox"]))
    state = machine.get_state()

    with pytest.raises(ValidationError):
        state.possible_cause = "Tamponade"

    with pytest.raises(ValidationError):
        state.causes[0].confidence = 0.0

    assert machine.get_state().possible_cause == "Hypoxia"
    assert machine.get_state().confidence == 0.82


def test_invalid_payload_confidence_falls_back_to_event_confidence() -> None:
    machine = ReversibleCauseStateMachine()
    event = _event(confidence="not-a-number", evidence=["manual-note"])

    state = machine.apply_event(event)

    assert state.confidence == event.confidence
