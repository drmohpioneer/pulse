from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from backend.workflow.engine import ClinicalWorkflowEngine
from backend.workflow.event_processor import MachineRegistry, RoutingTable
from backend.workflow.events import (
    ClinicalEvent,
    EventSource,
    EventStatus,
    EventType,
    Evidence,
)
from backend.workflow.rhythm import RhythmName, RhythmStateMachine
from backend.workflow.shocks import ShockStateMachine


T0 = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


def _speech_evidence(
    text: str,
    *,
    confidence: float,
    seconds: int = 0,
    evidence_type: str = "transcript_segment",
    payload: dict[str, Any] | None = None,
) -> Evidence:
    merged_payload = {"text": text}
    if payload:
        merged_payload.update(payload)
    return Evidence(
        source=EventSource.SPEECH,
        evidence_type=evidence_type,
        timestamp=T0 + timedelta(seconds=seconds),
        confidence=confidence,
        payload=merged_payload,
        raw_reference=text,
    )


def _acoustic_evidence(
    observation_type: str,
    *,
    confidence: float,
    seconds: int = 0,
) -> Evidence:
    return Evidence(
        source=EventSource.ACOUSTIC,
        evidence_type=observation_type,
        timestamp=T0 + timedelta(seconds=seconds),
        confidence=confidence,
        payload={"observation_type": observation_type},
        raw_reference=observation_type,
    )


def _manual_evidence(
    label: str,
    *,
    confidence: float = 1.0,
    seconds: int = 0,
) -> Evidence:
    return Evidence(
        source=EventSource.MANUAL,
        evidence_type="manual_confirmation",
        timestamp=T0 + timedelta(seconds=seconds),
        confidence=confidence,
        payload={"label": label},
        raw_reference=label,
    )


def _structured_evidence(
    *,
    source: EventSource = EventSource.SPEECH,
    event_type: EventType,
    observation_kind: str,
    confidence: float,
    payload: dict[str, Any] | None = None,
    seconds: int = 0,
    evidence_type: str = "normalized_clinical_observation",
    evidence_payload: dict[str, Any] | None = None,
) -> Evidence:
    full_payload = {
        "event_type": event_type.value,
        "payload": payload or {},
        "observation_kind": observation_kind,
    }
    if evidence_payload:
        full_payload.update(evidence_payload)
    return Evidence(
        source=source,
        evidence_type=evidence_type,
        timestamp=T0 + timedelta(seconds=seconds),
        confidence=confidence,
        payload=full_payload,
        raw_reference=observation_kind,
    )


def _device_evidence(
    evidence_type: str,
    *,
    event_type: EventType,
    confidence: float,
    payload: dict[str, Any] | None = None,
    seconds: int = 0,
) -> Evidence:
    return _structured_evidence(
        source=EventSource.DEVICE_FUTURE,
        event_type=event_type,
        observation_kind="completed_action",
        confidence=confidence,
        payload=payload,
        seconds=seconds,
        evidence_type=evidence_type,
    )


def _event(
    event_type: EventType,
    *,
    status: EventStatus,
    confidence: float,
    evidence: tuple[Evidence, ...],
    payload: dict[str, Any] | None = None,
    timestamp: datetime = T0,
) -> ClinicalEvent:
    return ClinicalEvent(
        event_type=event_type,
        source=evidence[0].source,
        status=status,
        confidence=confidence,
        evidence=evidence,
        payload=payload or {},
        timestamp=timestamp,
    )


def _engine_with_rhythm_and_shocks() -> ClinicalWorkflowEngine:
    registry = MachineRegistry()
    routing = RoutingTable(
        {
            EventType.RHYTHM_CHECKED: ("rhythm", "shocks"),
            EventType.SHOCK_DELIVERED: ("shocks",),
        }
    )
    engine = ClinicalWorkflowEngine(registry=registry, routing_table=routing)
    engine.register_machine("rhythm", RhythmStateMachine())
    engine.register_machine("shocks", ShockStateMachine())
    return engine


def _extractor() -> Any:
    from backend.audio.transcript_extractor import TranscriptPhraseEventExtractor

    return TranscriptPhraseEventExtractor()


def _fusion_engine() -> Any:
    from backend.services.evidence_fusion import DeterministicEvidenceFusionEngine

    return DeterministicEvidenceFusionEngine()


def test_transcript_phrase_extractor_emits_candidate_clinical_event_without_engine_mutation() -> None:
    extractor = _extractor()
    events = extractor.extract(
        _speech_evidence(
            "Shock delivered.",
            confidence=0.86,
            payload={"speaker_label": "speaker_1"},
        )
    )

    assert len(events) == 1
    event = events[0]
    assert event.event_type == EventType.SHOCK_DELIVERED
    assert event.source == EventSource.SPEECH
    assert event.status == EventStatus.CANDIDATE
    assert event.evidence[0].payload["speaker_label"] == "speaker_1"
    assert event.payload["extraction_kind"] == "completed_action"


def test_transcript_extractor_distinguishes_command_from_completed_medication() -> None:
    extractor = _extractor()

    command_events = extractor.extract(
        _speech_evidence("Give epinephrine.", confidence=0.9)
    )
    completed_events = extractor.extract(
        _speech_evidence("Epinephrine is in.", confidence=0.9)
    )

    assert command_events[0].event_type == EventType.MEDICATION_GIVEN
    assert command_events[0].status == EventStatus.CANDIDATE
    assert command_events[0].payload["medication"] == "epinephrine"
    assert command_events[0].payload["extraction_kind"] == "command"

    assert completed_events[0].event_type == EventType.MEDICATION_GIVEN
    assert completed_events[0].status == EventStatus.CANDIDATE
    assert completed_events[0].payload["medication"] == "epinephrine"
    assert completed_events[0].payload["extraction_kind"] == "completed_action"
    assert completed_events[0].confidence > command_events[0].confidence


@pytest.mark.parametrize(
    "text",
    [
        "the team appears ready",
        "we peaked at 40 compressions",
        "bring the vfib pads box",
    ],
)
def test_transcript_extractor_does_not_match_keywords_inside_words(text: str) -> None:
    assert _extractor().extract(_speech_evidence(text, confidence=0.95)) == ()


@pytest.mark.parametrize(
    ("text", "rhythm"),
    [
        ("rhythm is pea", "pea"),
        ("patient is vf", "vf"),
    ],
)
def test_transcript_extractor_matches_rhythm_keywords_as_words(
    text: str,
    rhythm: str,
) -> None:
    events = _extractor().extract(_speech_evidence(text, confidence=0.95))

    assert len(events) == 1
    assert events[0].event_type == EventType.RHYTHM_CHECKED
    assert events[0].payload["rhythm"] == rhythm


@pytest.mark.parametrize(
    "instruction",
    [
        "Resume CPR.",
        "Continue CPR.",
        "Continue CPR for 2 minutes.",
        "Continue compressions",
        "Continue the CPR",
        "Back on the chest",
        "Compressions back on",
        "Carry on CPR",
        "Go back on CPR",
    ],
)
def test_cpr_recommendation_instructions_are_accepted_transcript_phrases(
    instruction: str,
) -> None:
    events = _extractor().extract(_speech_evidence(instruction, confidence=0.95))

    assert len(events) == 1
    assert events[0].event_type == EventType.CPR_RESUMED
    assert events[0].payload["extraction_kind"] == "completed_action"


def test_transcript_extraction_is_deterministic_for_same_segment() -> None:
    extractor = _extractor()
    segment = _speech_evidence(
        "Rhythm is VF.",
        confidence=0.91,
        payload={"segment_id": "segment-123"},
    )

    first = extractor.extract(segment)
    second = extractor.extract(segment)

    assert [event.model_dump(exclude={"id", "created_at"}) for event in first] == [
        event.model_dump(exclude={"id", "created_at"}) for event in second
    ]


def test_fusion_rejects_low_confidence_speech_only_high_impact_event() -> None:
    fusion = _fusion_engine()
    result = fusion.fuse(
        [
            _speech_evidence("Shock delivered.", confidence=0.38),
        ]
    )

    assert result.candidate_event is not None
    assert result.candidate_event.event_type == EventType.SHOCK_DELIVERED
    assert result.candidate_event.status == EventStatus.REJECTED
    assert result.requires_confirmation is False
    assert result.uncertainty_reason is not None


def test_fusion_auto_accepts_medium_confidence_rhythm_identification() -> None:
    fusion = _fusion_engine()
    result = fusion.fuse(
        [
            _speech_evidence("Rhythm is VF.", confidence=0.72),
        ]
    )

    assert result.candidate_event is not None
    assert result.candidate_event.event_type == EventType.RHYTHM_CHECKED
    assert result.candidate_event.status == EventStatus.ACCEPTED
    assert result.requires_confirmation is False
    assert result.candidate_event.payload["rhythm"] == "vf"


def test_fusion_accepts_agreeing_independent_sources_without_message_text_matching() -> None:
    fusion = _fusion_engine()
    speech = _speech_evidence("Shock delivered.", confidence=0.82)
    acoustic = _acoustic_evidence("defibrillator_discharge", confidence=0.84)
    result = fusion.fuse([speech, acoustic])

    assert result.candidate_event is not None
    assert result.candidate_event.event_type == EventType.SHOCK_DELIVERED
    assert result.candidate_event.status == EventStatus.ACCEPTED
    assert result.candidate_event.confidence > max(speech.confidence, acoustic.confidence)
    assert set(result.evidence_ids) == {str(speech.id), str(acoustic.id)}
    assert result.requires_confirmation is False


def test_fusion_conflict_between_sources_requires_confirmation() -> None:
    fusion = _fusion_engine()
    result = fusion.fuse(
        [
            _speech_evidence("Shock delivered.", confidence=0.83),
            _manual_evidence("No shock was delivered.", confidence=1.0),
        ]
    )

    assert result.candidate_event is not None
    assert result.candidate_event.status == EventStatus.NEEDS_CONFIRMATION
    assert result.requires_confirmation is True
    assert "conflict" in (result.uncertainty_reason or "").lower()


def test_fusion_does_not_use_command_to_accept_completed_action_when_completed_first() -> None:
    fusion = _fusion_engine()
    completed = _structured_evidence(
        event_type=EventType.MEDICATION_GIVEN,
        observation_kind="completed_action",
        confidence=0.88,
        payload={"medication": "epinephrine"},
    )
    command = _structured_evidence(
        event_type=EventType.MEDICATION_GIVEN,
        observation_kind="command",
        confidence=0.88,
        payload={"medication": "epinephrine"},
        seconds=1,
    )

    result = fusion.fuse([completed, command])

    assert result.candidate_event is not None
    assert result.candidate_event.event_type == EventType.MEDICATION_GIVEN
    assert result.candidate_event.payload["medication"] == "epinephrine"
    assert result.candidate_event.confidence == completed.confidence
    assert result.candidate_event.status == EventStatus.ACCEPTED
    assert result.requires_confirmation is False
    assert set(result.evidence_ids) == {str(completed.id), str(command.id)}


def test_fusion_does_not_use_command_to_accept_completed_action_when_command_first() -> None:
    fusion = _fusion_engine()
    command = _structured_evidence(
        event_type=EventType.MEDICATION_GIVEN,
        observation_kind="command",
        confidence=0.88,
        payload={"medication": "epinephrine"},
    )
    completed = _structured_evidence(
        event_type=EventType.MEDICATION_GIVEN,
        observation_kind="completed_action",
        confidence=0.88,
        payload={"medication": "epinephrine"},
        seconds=1,
    )

    result = fusion.fuse([command, completed])

    assert result.candidate_event is not None
    assert result.candidate_event.event_type == EventType.MEDICATION_GIVEN
    assert result.candidate_event.payload["medication"] == "epinephrine"
    assert result.candidate_event.confidence == completed.confidence
    assert result.candidate_event.status == EventStatus.ACCEPTED
    assert result.requires_confirmation is False
    assert set(result.evidence_ids) == {str(command.id), str(completed.id)}


def test_high_confidence_speech_only_medication_completed_action_auto_accepts() -> None:
    result = _fusion_engine().fuse(
        [
            _structured_evidence(
                event_type=EventType.MEDICATION_GIVEN,
                observation_kind="completed_action",
                confidence=0.98,
                payload={"medication": "epinephrine"},
            )
        ]
    )

    assert result.candidate_event is not None
    assert result.candidate_event.status == EventStatus.ACCEPTED
    assert result.requires_confirmation is False
    assert result.uncertainty_reason == "closed_loop_completion"


def test_high_confidence_speech_only_shock_completed_action_auto_accepts() -> None:
    result = _fusion_engine().fuse(
        [
            _structured_evidence(
                event_type=EventType.SHOCK_DELIVERED,
                observation_kind="completed_action",
                confidence=0.98,
            )
        ]
    )

    assert result.candidate_event is not None
    assert result.candidate_event.status == EventStatus.ACCEPTED
    assert result.requires_confirmation is False
    assert result.uncertainty_reason == "closed_loop_completion"


def test_speech_shock_plus_acoustic_discharge_may_be_accepted() -> None:
    speech = _structured_evidence(
        event_type=EventType.SHOCK_DELIVERED,
        observation_kind="completed_action",
        confidence=0.86,
    )
    acoustic = _acoustic_evidence("defibrillator_discharge", confidence=0.84)

    result = _fusion_engine().fuse([speech, acoustic])

    assert result.candidate_event is not None
    assert result.candidate_event.status == EventStatus.ACCEPTED
    assert result.requires_confirmation is False


def test_speech_medication_plus_manual_confirmation_may_be_accepted() -> None:
    speech = _structured_evidence(
        event_type=EventType.MEDICATION_GIVEN,
        observation_kind="completed_action",
        confidence=0.86,
        payload={"medication": "epinephrine"},
    )
    manual = _structured_evidence(
        source=EventSource.MANUAL,
        event_type=EventType.MEDICATION_GIVEN,
        observation_kind="completed_action",
        confidence=1.0,
        payload={"medication": "epinephrine"},
        evidence_type="manual_confirmation",
    )

    result = _fusion_engine().fuse([speech, manual])

    assert result.candidate_event is not None
    assert result.candidate_event.status == EventStatus.ACCEPTED
    assert result.requires_confirmation is False


def test_manual_structured_completed_action_auto_accepts_high_impact_event() -> None:
    speech = _structured_evidence(
        event_type=EventType.MEDICATION_GIVEN,
        observation_kind="completed_action",
        confidence=0.86,
        payload={"medication": "epinephrine"},
    )
    manual_observation = _structured_evidence(
        source=EventSource.MANUAL,
        event_type=EventType.MEDICATION_GIVEN,
        observation_kind="completed_action",
        confidence=1.0,
        payload={"medication": "epinephrine"},
    )

    result = _fusion_engine().fuse([speech, manual_observation])

    assert result.candidate_event is not None
    assert result.candidate_event.status == EventStatus.ACCEPTED
    assert result.requires_confirmation is False


def test_device_future_unrelated_evidence_does_not_block_closed_loop_acceptance() -> None:
    speech = _structured_evidence(
        event_type=EventType.SHOCK_DELIVERED,
        observation_kind="completed_action",
        confidence=0.86,
    )
    unrelated_device = _device_evidence(
        "battery_status",
        event_type=EventType.SHOCK_DELIVERED,
        confidence=1.0,
    )

    result = _fusion_engine().fuse([speech, unrelated_device])

    assert result.candidate_event is not None
    assert result.candidate_event.status == EventStatus.ACCEPTED
    assert result.requires_confirmation is False


def test_device_future_allowlisted_shock_evidence_supports_shock_acceptance() -> None:
    speech = _structured_evidence(
        event_type=EventType.SHOCK_DELIVERED,
        observation_kind="completed_action",
        confidence=0.86,
    )
    device = _device_evidence(
        "defibrillator_discharge",
        event_type=EventType.SHOCK_DELIVERED,
        confidence=0.84,
    )

    result = _fusion_engine().fuse([speech, device])

    assert result.candidate_event is not None
    assert result.candidate_event.status == EventStatus.ACCEPTED
    assert result.requires_confirmation is False


def test_acoustic_discharge_label_from_wrong_source_does_not_become_shock_evidence() -> None:
    wrong_source = Evidence(
        source=EventSource.SPEECH,
        evidence_type="defibrillator_discharge",
        timestamp=T0,
        confidence=1.0,
        payload={"observation_type": "defibrillator_discharge"},
        raw_reference="defibrillator_discharge",
    )

    result = _fusion_engine().fuse([wrong_source])

    assert result.candidate_event is None
    assert result.uncertainty_reason == "no_clinical_interpretation"


def test_rhythm_speech_only_requires_confirmation() -> None:
    result = _fusion_engine().fuse(
        [
            _structured_evidence(
                event_type=EventType.RHYTHM_CHECKED,
                observation_kind="observation",
                confidence=0.98,
                payload={"rhythm": "vf"},
            )
        ]
    )

    assert result.candidate_event is not None
    assert result.candidate_event.status == EventStatus.NEEDS_CONFIRMATION
    assert result.requires_confirmation is True


def test_rosc_speech_only_requires_confirmation() -> None:
    result = _fusion_engine().fuse(
        [
            _structured_evidence(
                event_type=EventType.ROSC_ACHIEVED,
                observation_kind="observation",
                confidence=0.98,
                payload={"rhythm": "rosc"},
            )
        ]
    )

    assert result.candidate_event is not None
    assert result.candidate_event.status == EventStatus.NEEDS_CONFIRMATION
    assert result.requires_confirmation is True


def test_command_only_high_impact_evidence_does_not_accept() -> None:
    result = _fusion_engine().fuse(
        [
            _structured_evidence(
                event_type=EventType.MEDICATION_GIVEN,
                observation_kind="command",
                confidence=1.0,
                payload={"medication": "epinephrine"},
            )
        ]
    )

    assert result.candidate_event is not None
    assert result.candidate_event.status == EventStatus.NEEDS_CONFIRMATION
    assert result.requires_confirmation is True
    assert result.uncertainty_reason == "command_is_not_completed_action"


def test_low_impact_completed_action_keeps_threshold_acceptance_behavior() -> None:
    result = _fusion_engine().fuse(
        [
            _structured_evidence(
                event_type=EventType.CPR_RESUMED,
                observation_kind="completed_action",
                confidence=0.98,
            )
        ]
    )

    assert result.candidate_event is not None
    assert result.candidate_event.status == EventStatus.ACCEPTED
    assert result.requires_confirmation is False


def test_conflicting_high_impact_evidence_requires_confirmation() -> None:
    speech = _structured_evidence(
        event_type=EventType.SHOCK_DELIVERED,
        observation_kind="completed_action",
        confidence=0.98,
    )
    no_shock = _manual_evidence("No shock was delivered.", confidence=1.0)

    result = _fusion_engine().fuse([speech, no_shock])

    assert result.candidate_event is not None
    assert result.candidate_event.status == EventStatus.NEEDS_CONFIRMATION
    assert result.requires_confirmation is True
    assert result.uncertainty_reason == "conflicting_evidence"


def test_fusion_output_is_deterministic_for_same_evidence_order() -> None:
    fusion = _fusion_engine()
    evidence = [
        _speech_evidence("Rhythm is pVT.", confidence=0.88),
        _manual_evidence("Confirmed pVT.", confidence=1.0),
    ]

    first = fusion.fuse(evidence)
    second = fusion.fuse(evidence)

    assert first.model_dump(exclude={"candidate_event": {"id", "created_at"}}) == (
        second.model_dump(exclude={"candidate_event": {"id", "created_at"}})
    )


def test_nonaccepted_voice_or_fusion_events_do_not_mutate_workflow_state() -> None:
    engine = _engine_with_rhythm_and_shocks()
    candidate_vf = _event(
        EventType.RHYTHM_CHECKED,
        status=EventStatus.CANDIDATE,
        confidence=0.82,
        evidence=(_speech_evidence("Rhythm is VF.", confidence=0.82),),
        payload={"rhythm": "vf"},
    )
    needs_confirmation_shock = _event(
        EventType.SHOCK_DELIVERED,
        status=EventStatus.NEEDS_CONFIRMATION,
        confidence=0.74,
        evidence=(_acoustic_evidence("defibrillator_discharge", confidence=0.74),),
    )
    rejected_shock = _event(
        EventType.SHOCK_DELIVERED,
        status=EventStatus.REJECTED,
        confidence=0.22,
        evidence=(_speech_evidence("maybe shock", confidence=0.22),),
    )

    for event in (candidate_vf, needs_confirmation_shock, rejected_shock):
        result = engine.process(event)
        assert result.ok

    assert engine.get_machine_state("rhythm").current_rhythm == RhythmName.UNKNOWN
    assert engine.get_machine_state("shocks").shock_count == 0


def test_accepted_fused_event_is_the_only_phase2_output_that_mutates_workflow_state() -> None:
    engine = _engine_with_rhythm_and_shocks()
    accepted_vf = _event(
        EventType.RHYTHM_CHECKED,
        status=EventStatus.ACCEPTED,
        confidence=0.95,
        evidence=(
            _speech_evidence("Rhythm is VF.", confidence=0.86),
            _manual_evidence("VF confirmed."),
        ),
        payload={"rhythm": "vf"},
    )

    result = engine.process(accepted_vf)

    assert result.ok
    assert engine.get_machine_state("rhythm").current_rhythm == RhythmName.VF
    assert engine.get_machine_state("shocks").latest_rhythm_check_event_id == accepted_vf.id


def test_replay_is_unaffected_by_phase2_candidate_and_rejected_events() -> None:
    engine = _engine_with_rhythm_and_shocks()
    events = [
        _event(
            EventType.RHYTHM_CHECKED,
            status=EventStatus.CANDIDATE,
            confidence=0.8,
            evidence=(_speech_evidence("Rhythm might be VF.", confidence=0.8),),
            payload={"rhythm": "vf"},
            timestamp=T0,
        ),
        _event(
            EventType.RHYTHM_CHECKED,
            status=EventStatus.ACCEPTED,
            confidence=0.96,
            evidence=(_manual_evidence("VF confirmed.", seconds=2),),
            payload={"rhythm": "vf"},
            timestamp=T0 + timedelta(seconds=2),
        ),
        _event(
            EventType.SHOCK_DELIVERED,
            status=EventStatus.REJECTED,
            confidence=0.2,
            evidence=(_acoustic_evidence("artifact", confidence=0.2, seconds=4),),
            timestamp=T0 + timedelta(seconds=4),
        ),
    ]

    for event in events:
        engine.process(event)
    live_rhythm_state = engine.get_machine_state("rhythm")
    live_shock_state = engine.get_machine_state("shocks")

    replayed_rhythm = RhythmStateMachine().replay(events)
    replayed_shocks = ShockStateMachine().replay(events)

    assert replayed_rhythm == live_rhythm_state
    assert replayed_shocks == live_shock_state
    assert replayed_shocks.shock_count == 0
