from datetime import UTC, datetime, timedelta

import pytest

from backend.audio.multimodal import (
    DeterministicMedicalPhraseNormalizer,
    MultilingualTranscriptSegment,
    TranscriptLanguage,
)
from backend.audio.transcription import ObservationKind
from backend.services.multimodal_voice_pipeline import (
    DeterministicMultimodalVoicePipeline,
    MultimodalTranscriptIngestRequest,
)
from backend.workflow.cpr import CPRStateMachine, CPRStatus
from backend.workflow.engine import ClinicalWorkflowEngine
from backend.workflow.event_processor import MachineRegistry, RoutingTable
from backend.workflow.events import EventSource, EventStatus, EventType, Evidence
from backend.workflow.shocks import ShockStateMachine


T0 = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


def _segment(
    text: str,
    *,
    language: TranscriptLanguage = TranscriptLanguage.UNKNOWN,
    confidence: float = 1.0,
) -> MultilingualTranscriptSegment:
    return MultilingualTranscriptSegment(
        text=text,
        language=language,
        confidence=confidence,
        started_at=T0,
        ended_at=T0 + timedelta(seconds=1),
    )


def _structured_evidence(
    *,
    source: EventSource = EventSource.SPEECH,
    evidence_type: str = "normalized_clinical_observation",
    event_type: EventType,
    confidence: float,
    seconds: int = 0,
    payload: dict | None = None,
    observation_kind: str = "completed_action",
    is_positive: bool = True,
) -> Evidence:
    return Evidence(
        source=source,
        evidence_type=evidence_type,
        timestamp=T0 + timedelta(seconds=seconds),
        confidence=confidence,
        payload={
            "event_type": event_type.value,
            "payload": payload or {},
            "observation_kind": observation_kind,
            "is_positive": is_positive,
        },
        raw_reference=observation_kind,
    )


def _acoustic_shock_evidence(*, confidence: float, seconds: int = 0) -> Evidence:
    return Evidence(
        source=EventSource.ACOUSTIC,
        evidence_type="defibrillator_discharge",
        timestamp=T0 + timedelta(seconds=seconds),
        confidence=confidence,
        payload={"observation_type": "defibrillator_discharge"},
        raw_reference="defibrillator_discharge",
    )


def _manual_no_shock_evidence(*, confidence: float, seconds: int = 0) -> Evidence:
    return Evidence(
        source=EventSource.MANUAL,
        evidence_type="manual_confirmation",
        timestamp=T0 + timedelta(seconds=seconds),
        confidence=confidence,
        payload={"label": "No shock was delivered."},
        raw_reference="No shock was delivered.",
    )


def test_arabic_english_and_mixed_phrases_normalize_to_clinical_observations() -> None:
    normalizer = DeterministicMedicalPhraseNormalizer()
    cases = [
        (
            "ادي ادرينالين",
            EventType.MEDICATION_GIVEN,
            {"medication": "epinephrine"},
            ObservationKind.COMMAND,
        ),
        (
            "ادي ابي واحد ملي",
            EventType.MEDICATION_GIVEN,
            {"medication": "epinephrine", "dose": 1, "unit": "mg"},
            ObservationKind.COMMAND,
        ),
        (
            "give epi",
            EventType.MEDICATION_GIVEN,
            {"medication": "epinephrine"},
            ObservationKind.COMMAND,
        ),
        (
            "give adrenaline",
            EventType.MEDICATION_GIVEN,
            {"medication": "epinephrine"},
            ObservationKind.COMMAND,
        ),
        ("shock اتعمل", EventType.SHOCK_DELIVERED, {}, ObservationKind.COMPLETED_ACTION),
        ("ارجع CPR", EventType.CPR_RESUMED, {}, ObservationKind.COMPLETED_ACTION),
        ("في نبض", EventType.ROSC_ACHIEVED, {"rhythm": "rosc"}, ObservationKind.OBSERVATION),
        ("ROSC حصل", EventType.ROSC_ACHIEVED, {"rhythm": "rosc"}, ObservationKind.COMPLETED_ACTION),
    ]

    for text, event_type, payload, kind in cases:
        observations = normalizer.normalize(_segment(text))

        assert len(observations) == 1
        assert observations[0].event_type == event_type
        assert observations[0].payload == payload
        assert observations[0].observation_kind == kind


@pytest.mark.parametrize(
    ("text", "event_type", "payload", "kind"),
    [
        ("give epinephrine", EventType.MEDICATION_GIVEN, {"medication": "epinephrine"}, ObservationKind.COMMAND),
        ("ادي ابي", EventType.MEDICATION_GIVEN, {"medication": "epinephrine"}, ObservationKind.COMMAND),
        ("epi is in", EventType.MEDICATION_GIVEN, {"medication": "epinephrine"}, ObservationKind.COMPLETED_ACTION),
        ("adrenaline given", EventType.MEDICATION_GIVEN, {"medication": "epinephrine"}, ObservationKind.COMPLETED_ACTION),
        ("epinephrine given", EventType.MEDICATION_GIVEN, {"medication": "epinephrine"}, ObservationKind.COMPLETED_ACTION),
        ("ادرينالين اتدى", EventType.MEDICATION_GIVEN, {"medication": "epinephrine"}, ObservationKind.COMPLETED_ACTION),
        ("الادرينالين دخل", EventType.MEDICATION_GIVEN, {"medication": "epinephrine"}, ObservationKind.COMPLETED_ACTION),
        ("ادينا ادرينالين", EventType.MEDICATION_GIVEN, {"medication": "epinephrine"}, ObservationKind.COMPLETED_ACTION),
        ("ادي اميو", EventType.MEDICATION_GIVEN, {"medication": "amiodarone"}, ObservationKind.COMMAND),
        ("amio given", EventType.MEDICATION_GIVEN, {"medication": "amiodarone"}, ObservationKind.COMPLETED_ACTION),
        ("amiodarone given", EventType.MEDICATION_GIVEN, {"medication": "amiodarone"}, ObservationKind.COMPLETED_ACTION),
        ("lidocaine given", EventType.MEDICATION_GIVEN, {"medication": "lidocaine"}, ObservationKind.COMPLETED_ACTION),
        ("lido is in", EventType.MEDICATION_GIVEN, {"medication": "lidocaine"}, ObservationKind.COMPLETED_ACTION),
    ],
)
def test_expanded_medication_phrases_normalize_safely(
    text: str,
    event_type: EventType,
    payload: dict,
    kind: ObservationKind,
) -> None:
    observations = DeterministicMedicalPhraseNormalizer().normalize(_segment(text))

    assert len(observations) == 1
    assert observations[0].event_type == event_type
    assert observations[0].payload == payload
    assert observations[0].observation_kind == kind


@pytest.mark.parametrize(
    ("text", "payload", "kind"),
    [
        ("epi 1 mg", {"medication": "epinephrine", "dose": 1, "unit": "mg"}, ObservationKind.OBSERVATION),
        ("epinephrine 1 milligram", {"medication": "epinephrine", "dose": 1, "unit": "mg"}, ObservationKind.OBSERVATION),
        ("adrenaline one milligram", {"medication": "epinephrine", "dose": 1, "unit": "mg"}, ObservationKind.OBSERVATION),
        ("give epi 1 mg", {"medication": "epinephrine", "dose": 1, "unit": "mg"}, ObservationKind.COMMAND),
        ("epi is in 1 mg", {"medication": "epinephrine", "dose": 1, "unit": "mg"}, ObservationKind.COMPLETED_ACTION),
        ("ادي ادرينالين واحد ملي", {"medication": "epinephrine", "dose": 1, "unit": "mg"}, ObservationKind.COMMAND),
        ("ادرينالين 1 ملي", {"medication": "epinephrine", "dose": 1, "unit": "mg"}, ObservationKind.OBSERVATION),
        ("الادرينالين دخل واحد ملي", {"medication": "epinephrine", "dose": 1, "unit": "mg"}, ObservationKind.COMPLETED_ACTION),
        ("amiodarone 300 mg", {"medication": "amiodarone", "dose": 300, "unit": "mg"}, ObservationKind.OBSERVATION),
        ("amio 300", {"medication": "amiodarone", "dose": 300, "unit": "mg"}, ObservationKind.OBSERVATION),
        ("amio is in 300", {"medication": "amiodarone", "dose": 300, "unit": "mg"}, ObservationKind.COMPLETED_ACTION),
        ("ادي اميو 300", {"medication": "amiodarone", "dose": 300, "unit": "mg"}, ObservationKind.COMMAND),
        ("اميو 300 اتدى", {"medication": "amiodarone", "dose": 300, "unit": "mg"}, ObservationKind.COMPLETED_ACTION),
        ("lidocaine 100 mg", {"medication": "lidocaine", "dose": 100, "unit": "mg"}, ObservationKind.OBSERVATION),
        ("lido 100", {"medication": "lidocaine", "dose": 100, "unit": "mg"}, ObservationKind.OBSERVATION),
        ("lido is in 100", {"medication": "lidocaine", "dose": 100, "unit": "mg"}, ObservationKind.COMPLETED_ACTION),
        ("ادي ليدوكايين 100", {"medication": "lidocaine", "dose": 100, "unit": "mg"}, ObservationKind.COMMAND),
    ],
)
def test_medication_dose_phrases_extract_deterministic_payload(
    text: str,
    payload: dict,
    kind: ObservationKind,
) -> None:
    observations = DeterministicMedicalPhraseNormalizer().normalize(_segment(text))

    assert len(observations) == 1
    assert observations[0].event_type == EventType.MEDICATION_GIVEN
    assert observations[0].payload == payload
    assert observations[0].observation_kind == kind


@pytest.mark.parametrize(
    ("text", "expected_route"),
    [
        ("give epi 1 mg IV", "IV"),
        ("epi is in 1 mg IV push", "IV"),
        ("adrenaline one milligram through the IO", "IO"),
        ("ادي ادرينالين واحد ملي وريدي", "IV"),
        ("ادي ادرينالين واحد ملي عن طريق الوريد", "IV"),
        ("amio 300 IO", "IO"),
    ],
)
def test_medication_route_phrases_extract_only_clear_route(
    text: str,
    expected_route: str,
) -> None:
    observation = DeterministicMedicalPhraseNormalizer().normalize(_segment(text))[0]

    assert observation.payload["route"] == expected_route


def test_ambiguous_medication_route_is_not_inferred() -> None:
    observation = DeterministicMedicalPhraseNormalizer().normalize(
        _segment("epi 1 mg IV IO")
    )[0]

    assert "route" not in observation.payload


@pytest.mark.parametrize("text", ["not adrenaline 1 mg", "مش ادرينالين واحد ملي"])
def test_negated_medication_with_dose_does_not_create_positive_observation(
    text: str,
) -> None:
    observation = DeterministicMedicalPhraseNormalizer().normalize(_segment(text))[0]

    assert observation.event_type == EventType.MEDICATION_GIVEN
    assert observation.observation_kind == ObservationKind.CORRECTION
    assert observation.payload == {"medication": "epinephrine", "is_positive": False}


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("give epi 1 mg IV", ObservationKind.COMMAND),
        ("epi is in 1 mg IV", ObservationKind.COMPLETED_ACTION),
    ],
)
def test_medication_dose_route_extraction_preserves_confirmation_policy(
    text: str,
    kind: ObservationKind,
) -> None:
    result = DeterministicMultimodalVoicePipeline().ingest_transcript(
        MultimodalTranscriptIngestRequest(
            text=text,
            confidence=1.0,
            language=TranscriptLanguage.MIXED,
            timestamp=T0,
        )
    )

    event = result.fusion_results[0].candidate_event
    assert event is not None
    assert event.payload["dose"] == 1
    assert event.payload["unit"] == "mg"
    assert event.payload["route"] == "IV"
    expected_status = (
        EventStatus.ACCEPTED
        if kind == ObservationKind.COMPLETED_ACTION
        else EventStatus.NEEDS_CONFIRMATION
    )
    assert event.status == expected_status
    assert result.evidence[0].payload["observation_kind"] == kind.value
    assert bool(result.accepted_events) is (kind == ObservationKind.COMPLETED_ACTION)


@pytest.mark.parametrize(
    ("text", "event_type", "kind"),
    [
        ("shock delivered", EventType.SHOCK_DELIVERED, ObservationKind.COMPLETED_ACTION),
        ("shocked", EventType.SHOCK_DELIVERED, ObservationKind.COMPLETED_ACTION),
        ("defibrillated", EventType.SHOCK_DELIVERED, ObservationKind.COMPLETED_ACTION),
        ("اتعمل shock", EventType.SHOCK_DELIVERED, ObservationKind.COMPLETED_ACTION),
        ("صدمة اتعملت", EventType.SHOCK_DELIVERED, ObservationKind.COMPLETED_ACTION),
        ("اشحن", EventType.SHOCK_DELIVERED, ObservationKind.COMMAND),
        ("shock now", EventType.SHOCK_DELIVERED, ObservationKind.COMMAND),
        ("charging", EventType.SHOCK_DELIVERED, ObservationKind.INTENT),
        ("charged", EventType.SHOCK_DELIVERED, ObservationKind.INTENT),
        ("start CPR", EventType.CPR_STARTED, ObservationKind.COMPLETED_ACTION),
        ("ابدأ CPR", EventType.CPR_STARTED, ObservationKind.COMPLETED_ACTION),
        ("CPR started", EventType.CPR_STARTED, ObservationKind.COMPLETED_ACTION),
        ("resume CPR", EventType.CPR_RESUMED, ObservationKind.COMPLETED_ACTION),
        ("continue CPR", EventType.CPR_RESUMED, ObservationKind.COMPLETED_ACTION),
        ("continue CPR for 2 minutes", EventType.CPR_RESUMED, ObservationKind.COMPLETED_ACTION),
        ("continue compressions", EventType.CPR_RESUMED, ObservationKind.COMPLETED_ACTION),
        ("continue the CPR", EventType.CPR_RESUMED, ObservationKind.COMPLETED_ACTION),
        ("back on the chest", EventType.CPR_RESUMED, ObservationKind.COMPLETED_ACTION),
        ("compressions back on", EventType.CPR_RESUMED, ObservationKind.COMPLETED_ACTION),
        ("carry on CPR", EventType.CPR_RESUMED, ObservationKind.COMPLETED_ACTION),
        ("go back on CPR", EventType.CPR_RESUMED, ObservationKind.COMPLETED_ACTION),
        ("ارجع ضغط", EventType.CPR_RESUMED, ObservationKind.COMPLETED_ACTION),
        ("كمل ضغط", EventType.CPR_RESUMED, ObservationKind.COMPLETED_ACTION),
        ("compressions resumed", EventType.CPR_RESUMED, ObservationKind.COMPLETED_ACTION),
        ("وقف CPR", EventType.CPR_PAUSED, ObservationKind.COMPLETED_ACTION),
        ("pause compressions", EventType.CPR_PAUSED, ObservationKind.COMPLETED_ACTION),
    ],
)
def test_expanded_shock_and_cpr_phrases_preserve_observation_kind(
    text: str,
    event_type: EventType,
    kind: ObservationKind,
) -> None:
    observations = DeterministicMedicalPhraseNormalizer().normalize(_segment(text))

    assert len(observations) == 1
    assert observations[0].event_type == event_type
    assert observations[0].observation_kind == kind


@pytest.mark.parametrize(
    ("text", "payload"),
    [
        ("VF", {"rhythm": "vf"}),
        ("v fib", {"rhythm": "vf"}),
        ("ventricular fibrillation", {"rhythm": "vf"}),
        ("في VF", {"rhythm": "vf"}),
        ("الريذم VF", {"rhythm": "vf"}),
        ("pVT", {"rhythm": "pulseless_vt"}),
        ("pulseless VT", {"rhythm": "pulseless_vt"}),
        ("PEA", {"rhythm": "pea"}),
        ("في PEA", {"rhythm": "pea"}),
        ("الريذم PEA", {"rhythm": "pea"}),
        ("asystole", {"rhythm": "asystole"}),
        ("flatline", {"rhythm": "asystole"}),
        ("اسستولي", {"rhythm": "asystole"}),
        ("اسيستول", {"rhythm": "asystole"}),
    ],
)
def test_expanded_rhythm_phrases_normalize_as_rhythm_identifications(
    text: str,
    payload: dict,
) -> None:
    observations = DeterministicMedicalPhraseNormalizer().normalize(_segment(text))

    assert len(observations) == 1
    assert observations[0].event_type == EventType.RHYTHM_CHECKED
    assert observations[0].payload == payload
    assert observations[0].observation_kind == ObservationKind.RHYTHM_IDENTIFICATION


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("ROSC", ObservationKind.OBSERVATION),
        ("ROSC achieved", ObservationKind.COMPLETED_ACTION),
        ("return of spontaneous circulation", ObservationKind.COMPLETED_ACTION),
        ("we have a pulse", ObservationKind.OBSERVATION),
        ("pulse is back", ObservationKind.COMPLETED_ACTION),
        ("النبض رجع", ObservationKind.COMPLETED_ACTION),
        ("في pulse", ObservationKind.OBSERVATION),
    ],
)
def test_expanded_rosc_and_pulse_phrases_normalize_safely(
    text: str,
    kind: ObservationKind,
) -> None:
    observations = DeterministicMedicalPhraseNormalizer().normalize(_segment(text))

    assert len(observations) == 1
    assert observations[0].event_type == EventType.ROSC_ACHIEVED
    assert observations[0].payload == {"rhythm": "rosc"}
    assert observations[0].observation_kind == kind


@pytest.mark.parametrize(
    ("text", "event_type", "payload"),
    [
        ("no shock", EventType.SHOCK_DELIVERED, {}),
        ("ما اتعملش shock", EventType.SHOCK_DELIVERED, {}),
        ("shock ما اتعملش", EventType.SHOCK_DELIVERED, {}),
        ("not adrenaline", EventType.MEDICATION_GIVEN, {"medication": "epinephrine"}),
        ("مش ادرينالين", EventType.MEDICATION_GIVEN, {"medication": "epinephrine"}),
        ("no pulse", EventType.ROSC_ACHIEVED, {"rhythm": "rosc"}),
        ("مفيش نبض", EventType.ROSC_ACHIEVED, {"rhythm": "rosc"}),
    ],
)
def test_negation_and_correction_phrases_are_not_positive_completed_events(
    text: str,
    event_type: EventType,
    payload: dict,
) -> None:
    observation = DeterministicMedicalPhraseNormalizer().normalize(_segment(text))[0]

    assert observation.event_type == event_type
    assert observation.observation_kind == ObservationKind.CORRECTION
    assert observation.payload == {**payload, "is_positive": False}


@pytest.mark.parametrize("text", ["not given", "متدّاش", "cancel that"])
def test_ambiguous_corrections_do_not_create_clinical_observations(text: str) -> None:
    observations = DeterministicMedicalPhraseNormalizer().normalize(_segment(text))

    assert observations == ()


def test_diarization_placeholder_is_advisory_and_defaults_to_unknown_speaker() -> None:
    pipeline = DeterministicMultimodalVoicePipeline()

    result = pipeline.ingest_transcript(
        MultimodalTranscriptIngestRequest(
            text="give epi",
            confidence=1.0,
            language=TranscriptLanguage.ENGLISH,
            timestamp=T0,
        )
    )

    assert result.speaker_turns[0].speaker_id == "speaker_unknown"
    assert result.evidence[0].payload["speaker_id"] == "speaker_unknown"
    assert result.fusion_results[0].candidate_event is not None


def test_commands_never_auto_accept_as_completed_clinical_events() -> None:
    pipeline = DeterministicMultimodalVoicePipeline()

    result = pipeline.ingest_transcript(
        MultimodalTranscriptIngestRequest(
            text="give adrenaline",
            confidence=1.0,
            language=TranscriptLanguage.ENGLISH,
            timestamp=T0,
        )
    )

    event = result.fusion_results[0].candidate_event
    assert event is not None
    assert event.event_type == EventType.MEDICATION_GIVEN
    assert event.payload["medication"] == "epinephrine"
    assert event.confidence >= 0.9
    assert event.status == EventStatus.NEEDS_CONFIRMATION
    assert result.fusion_results[0].requires_confirmation is True
    assert result.accepted_events == ()


@pytest.mark.parametrize("text", ["ادي ادرينالين", "ادي ابي", "ادي اميو"])
def test_arabic_medication_commands_do_not_auto_accept_high_impact_events(
    text: str,
) -> None:
    result = DeterministicMultimodalVoicePipeline().ingest_transcript(
        MultimodalTranscriptIngestRequest(
            text=text,
            confidence=1.0,
            language=TranscriptLanguage.EGYPTIAN_ARABIC,
            timestamp=T0,
        )
    )

    event = result.fusion_results[0].candidate_event
    assert event is not None
    assert event.event_type == EventType.MEDICATION_GIVEN
    assert event.status == EventStatus.NEEDS_CONFIRMATION
    assert result.accepted_events == ()


@pytest.mark.parametrize(
    ("text", "event_type"),
    [
        ("الادرينالين دخل", EventType.MEDICATION_GIVEN),
        ("صدمة اتعملت", EventType.SHOCK_DELIVERED),
        ("النبض رجع", EventType.ROSC_ACHIEVED),
    ],
)
def test_arabic_completed_action_high_impact_speech_only_auto_accepts(
    text: str,
    event_type: EventType,
) -> None:
    result = DeterministicMultimodalVoicePipeline().ingest_transcript(
        MultimodalTranscriptIngestRequest(
            text=text,
            confidence=1.0,
            language=TranscriptLanguage.EGYPTIAN_ARABIC,
            timestamp=T0,
        )
    )

    event = result.fusion_results[0].candidate_event
    assert event is not None
    assert event.event_type == event_type
    assert event.status == EventStatus.ACCEPTED
    assert result.fusion_results[0].uncertainty_reason == "closed_loop_completion"
    assert result.accepted_events == (event,)


@pytest.mark.parametrize(
    ("text", "target_event_type"),
    [
        ("no shock", EventType.SHOCK_DELIVERED),
        ("ما اتعملش shock", EventType.SHOCK_DELIVERED),
        ("shock ما اتعملش", EventType.SHOCK_DELIVERED),
        ("no pulse", EventType.ROSC_ACHIEVED),
        ("مفيش نبض", EventType.ROSC_ACHIEVED),
        ("not adrenaline", EventType.MEDICATION_GIVEN),
        ("مش ادرينالين", EventType.MEDICATION_GIVEN),
    ],
)
def test_negative_only_pipeline_output_is_evidence_only_and_ui_safe(
    text: str,
    target_event_type: EventType,
) -> None:
    result = DeterministicMultimodalVoicePipeline().ingest_transcript(
        MultimodalTranscriptIngestRequest(
            text=text,
            confidence=1.0,
            language=TranscriptLanguage.MIXED,
            timestamp=T0,
        )
    )

    fusion_result = result.fusion_results[0]
    assert result.evidence[0].payload["is_positive"] is False
    assert result.evidence[0].id
    assert fusion_result.candidate_event is None
    assert fusion_result.requires_confirmation is False
    assert fusion_result.result_kind == "negative_evidence"
    assert fusion_result.is_negative_evidence is True
    assert fusion_result.correction_target_event_type == target_event_type
    assert fusion_result.uncertainty_reason == "negative_evidence_without_target"
    assert fusion_result.evidence_ids == [str(result.evidence[0].id)]
    assert result.confirmation_requests == ()
    assert result.accepted_events == ()


@pytest.mark.parametrize("text", ["not given", "cancel that"])
def test_ambiguous_untargeted_corrections_stay_out_of_fusion(
    text: str,
) -> None:
    result = DeterministicMultimodalVoicePipeline().ingest_transcript(
        MultimodalTranscriptIngestRequest(
            text=text,
            confidence=1.0,
            language=TranscriptLanguage.ENGLISH,
            timestamp=T0,
        )
    )

    assert result.evidence == ()
    assert result.evidence_groups == ()
    assert result.fusion_results == ()
    assert result.confirmation_requests == ()
    assert result.accepted_events == ()


def test_live_voice_normalization_covers_exact_and_natural_phrases() -> None:
    cases = [
        ("shock اتعمل", ((EventType.SHOCK_DELIVERED, {}),)),
        ("rhythm is asystole", ((EventType.RHYTHM_CHECKED, {"rhythm": "asystole"}),)),
        ("rhythm is pea", ((EventType.RHYTHM_CHECKED, {"rhythm": "pea"}),)),
        ("So rhythm is asystole.", ((EventType.RHYTHM_CHECKED, {"rhythm": "asystole"}),)),
        ("Patient is VF.", ((EventType.RHYTHM_CHECKED, {"rhythm": "vf"}),)),
        ("shock delivered guys", ((EventType.SHOCK_DELIVERED, {}),)),
        (
            "shock delivered resume cpr",
            (
                (EventType.SHOCK_DELIVERED, {}),
                (EventType.CPR_RESUMED, {}),
            ),
        ),
        ("okay everyone cpr started", ((EventType.CPR_STARTED, {}),)),
    ]

    for text, expected_observations in cases:
        result = DeterministicMultimodalVoicePipeline().ingest_transcript(
            MultimodalTranscriptIngestRequest(
                text=text,
                confidence=1.0,
                language=TranscriptLanguage.ENGLISH,
                timestamp=T0,
            )
        )

        assert tuple(
            (observation.event_type, dict(observation.payload))
            for observation in result.normalized_observations
        ) == expected_observations
        assert all(item.source.value == "speech" for item in result.evidence)
        assert len(result.fusion_results) == len(expected_observations)


def test_closed_loop_cpr_completion_is_accepted_before_engine_processing() -> None:
    pipeline = DeterministicMultimodalVoicePipeline()
    registry = MachineRegistry()
    routing = RoutingTable({EventType.CPR_RESUMED: ("cpr",)})
    engine = ClinicalWorkflowEngine(registry=registry, routing_table=routing)
    engine.register_machine("cpr", CPRStateMachine())

    result = pipeline.ingest_transcript(
        MultimodalTranscriptIngestRequest(
            text="ارجع CPR",
            confidence=1.0,
            language=TranscriptLanguage.MIXED,
            timestamp=T0,
        )
    )

    event = result.fusion_results[0].candidate_event
    assert event is not None
    assert event.status == EventStatus.ACCEPTED

    for accepted_event in result.accepted_events:
        engine.process(accepted_event)

    assert engine.get_machine_state("cpr").status == CPRStatus.ACTIVE


def test_only_accepted_perception_events_enter_the_clinical_engine() -> None:
    pipeline = DeterministicMultimodalVoicePipeline()
    registry = MachineRegistry()
    routing = RoutingTable({EventType.SHOCK_DELIVERED: ("shocks",)})
    engine = ClinicalWorkflowEngine(registry=registry, routing_table=routing)
    engine.register_machine("shocks", ShockStateMachine())

    needs_confirmation = pipeline.ingest_transcript(
        MultimodalTranscriptIngestRequest(
            text="shock اتعمل",
            confidence=0.95,
            language=TranscriptLanguage.MIXED,
            timestamp=T0,
        )
    )
    speech = _structured_evidence(
        event_type=EventType.SHOCK_DELIVERED,
        confidence=0.86,
        seconds=2,
    )
    acoustic = _acoustic_shock_evidence(confidence=0.84, seconds=3)
    accepted_result = pipeline.fuse_evidence_groups(
        pipeline.group_evidence((speech, acoustic))
    )[0]

    assert len(needs_confirmation.accepted_events) == 1
    assert accepted_result.candidate_event is not None
    assert accepted_result.candidate_event.status == EventStatus.ACCEPTED

    for accepted_event in (
        *needs_confirmation.accepted_events,
        accepted_result.candidate_event,
    ):
        if accepted_event.status == EventStatus.ACCEPTED:
            engine.process(accepted_event)

    assert engine.get_machine_state("shocks").shock_count == 2


def test_groups_speech_completed_action_with_acoustic_shock_discharge() -> None:
    pipeline = DeterministicMultimodalVoicePipeline()
    speech = _structured_evidence(
        event_type=EventType.SHOCK_DELIVERED,
        confidence=0.86,
    )
    acoustic = _acoustic_shock_evidence(confidence=0.84, seconds=3)

    groups = pipeline.group_evidence((speech, acoustic))
    results = pipeline.fuse_evidence_groups(groups)

    assert len(groups) == 1
    assert {item.id for item in groups[0]} == {speech.id, acoustic.id}
    assert len(results) == 1
    assert results[0].candidate_event is not None
    assert results[0].candidate_event.event_type == EventType.SHOCK_DELIVERED
    assert results[0].candidate_event.status == EventStatus.ACCEPTED
    assert set(results[0].evidence_ids) == {str(speech.id), str(acoustic.id)}


def test_groups_speech_completed_action_with_structured_manual_confirmation() -> None:
    pipeline = DeterministicMultimodalVoicePipeline()
    speech = _structured_evidence(
        event_type=EventType.SHOCK_DELIVERED,
        confidence=0.86,
    )
    manual = _structured_evidence(
        source=EventSource.MANUAL,
        evidence_type="manual_confirmation",
        event_type=EventType.SHOCK_DELIVERED,
        confidence=1.0,
        seconds=2,
    )

    groups = pipeline.group_evidence((speech, manual))
    results = pipeline.fuse_evidence_groups(groups)

    assert len(groups) == 1
    assert len(results) == 1
    assert results[0].candidate_event is not None
    assert results[0].candidate_event.status == EventStatus.ACCEPTED
    assert set(results[0].evidence_ids) == {str(speech.id), str(manual.id)}


def test_groups_command_with_completed_action_without_command_confidence_boost() -> None:
    pipeline = DeterministicMultimodalVoicePipeline()
    command = _structured_evidence(
        event_type=EventType.MEDICATION_GIVEN,
        confidence=0.88,
        payload={"medication": "epinephrine"},
        observation_kind="command",
    )
    completed = _structured_evidence(
        event_type=EventType.MEDICATION_GIVEN,
        confidence=0.88,
        seconds=1,
        payload={"medication": "epinephrine"},
        observation_kind="completed_action",
    )

    groups = pipeline.group_evidence((command, completed))
    results = pipeline.fuse_evidence_groups(groups)

    assert len(groups) == 1
    assert len(results) == 1
    assert results[0].candidate_event is not None
    assert results[0].candidate_event.confidence == completed.confidence
    assert results[0].candidate_event.status == EventStatus.ACCEPTED
    assert set(results[0].evidence_ids) == {str(command.id), str(completed.id)}


def test_incompatible_payloads_are_not_grouped() -> None:
    pipeline = DeterministicMultimodalVoicePipeline()
    epinephrine = _structured_evidence(
        event_type=EventType.MEDICATION_GIVEN,
        confidence=0.9,
        payload={"medication": "epinephrine"},
    )
    amiodarone = _structured_evidence(
        event_type=EventType.MEDICATION_GIVEN,
        confidence=0.9,
        seconds=1,
        payload={"medication": "amiodarone"},
    )

    groups = pipeline.group_evidence((epinephrine, amiodarone))

    assert len(groups) == 2
    assert {groups[0][0].id, groups[1][0].id} == {epinephrine.id, amiodarone.id}


def test_evidence_outside_grouping_window_is_not_grouped() -> None:
    pipeline = DeterministicMultimodalVoicePipeline()
    speech = _structured_evidence(
        event_type=EventType.SHOCK_DELIVERED,
        confidence=0.95,
    )
    late_acoustic = _acoustic_shock_evidence(confidence=0.95, seconds=11)

    groups = pipeline.group_evidence((speech, late_acoustic))

    assert len(groups) == 2
    assert {groups[0][0].id, groups[1][0].id} == {speech.id, late_acoustic.id}


def test_conflicting_evidence_inside_window_is_grouped_and_requires_confirmation() -> None:
    pipeline = DeterministicMultimodalVoicePipeline()
    speech = _structured_evidence(
        event_type=EventType.SHOCK_DELIVERED,
        confidence=0.9,
    )
    no_shock = _manual_no_shock_evidence(confidence=1.0, seconds=2)

    groups = pipeline.group_evidence((speech, no_shock))
    results = pipeline.fuse_evidence_groups(groups)

    assert len(groups) == 1
    assert len(results) == 1
    assert results[0].candidate_event is not None
    assert results[0].candidate_event.status == EventStatus.NEEDS_CONFIRMATION
    assert results[0].requires_confirmation is True
    assert results[0].uncertainty_reason == "conflicting_evidence"


def test_rhythm_payload_conflict_inside_window_is_one_confirmation_result() -> None:
    pipeline = DeterministicMultimodalVoicePipeline()
    vf = _structured_evidence(
        source=EventSource.MANUAL,
        evidence_type="manual_confirmation",
        event_type=EventType.RHYTHM_CHECKED,
        confidence=1.0,
        payload={"rhythm": "vf"},
    )
    pea = _structured_evidence(
        source=EventSource.MANUAL,
        evidence_type="manual_confirmation",
        event_type=EventType.RHYTHM_CHECKED,
        confidence=1.0,
        seconds=1,
        payload={"rhythm": "pea"},
    )

    groups = pipeline.group_evidence((vf, pea))
    results = pipeline.fuse_evidence_groups(groups)

    assert len(groups) == 1
    assert {item.id for item in groups[0]} == {vf.id, pea.id}
    assert len(results) == 1
    assert results[0].candidate_event is not None
    assert results[0].candidate_event.status == EventStatus.NEEDS_CONFIRMATION
    assert results[0].requires_confirmation is True
    assert results[0].uncertainty_reason == "conflicting_evidence"


def test_reversed_rhythm_payload_conflict_has_same_confirmation_result() -> None:
    pipeline = DeterministicMultimodalVoicePipeline()
    vf = _structured_evidence(
        source=EventSource.MANUAL,
        evidence_type="manual_confirmation",
        event_type=EventType.RHYTHM_CHECKED,
        confidence=1.0,
        payload={"rhythm": "vf"},
    )
    pea = _structured_evidence(
        source=EventSource.MANUAL,
        evidence_type="manual_confirmation",
        event_type=EventType.RHYTHM_CHECKED,
        confidence=1.0,
        seconds=1,
        payload={"rhythm": "pea"},
    )

    forward_groups = pipeline.group_evidence((vf, pea))
    reverse_groups = pipeline.group_evidence((pea, vf))
    forward_result = pipeline.fuse_evidence_groups(forward_groups)[0]
    reverse_result = pipeline.fuse_evidence_groups(reverse_groups)[0]

    assert len(forward_groups) == len(reverse_groups) == 1
    assert {item.id for item in forward_groups[0]} == {item.id for item in reverse_groups[0]}
    assert forward_result.candidate_event is not None
    assert reverse_result.candidate_event is not None
    assert forward_result.candidate_event.status == reverse_result.candidate_event.status
    assert forward_result.uncertainty_reason == reverse_result.uncertainty_reason


def test_same_medication_payload_conflict_inside_window_requires_confirmation() -> None:
    pipeline = DeterministicMultimodalVoicePipeline()
    epinephrine_one_mg = _structured_evidence(
        source=EventSource.MANUAL,
        evidence_type="manual_confirmation",
        event_type=EventType.MEDICATION_GIVEN,
        confidence=1.0,
        payload={"medication": "epinephrine", "dose": 1, "unit": "mg"},
    )
    epinephrine_ten_mg = _structured_evidence(
        source=EventSource.MANUAL,
        evidence_type="manual_confirmation",
        event_type=EventType.MEDICATION_GIVEN,
        confidence=1.0,
        seconds=1,
        payload={"medication": "epinephrine", "dose": 10, "unit": "mg"},
    )

    groups = pipeline.group_evidence((epinephrine_one_mg, epinephrine_ten_mg))
    results = pipeline.fuse_evidence_groups(groups)

    assert len(groups) == 1
    assert len(results) == 1
    assert results[0].candidate_event is not None
    assert results[0].candidate_event.status == EventStatus.NEEDS_CONFIRMATION
    assert results[0].uncertainty_reason == "conflicting_evidence"


def test_rosc_and_active_arrest_rhythm_inside_window_requires_confirmation() -> None:
    pipeline = DeterministicMultimodalVoicePipeline()
    rosc = _structured_evidence(
        source=EventSource.MANUAL,
        evidence_type="manual_confirmation",
        event_type=EventType.ROSC_ACHIEVED,
        confidence=1.0,
        payload={"rhythm": "rosc"},
    )
    vf = _structured_evidence(
        source=EventSource.MANUAL,
        evidence_type="manual_confirmation",
        event_type=EventType.RHYTHM_CHECKED,
        confidence=1.0,
        seconds=1,
        payload={"rhythm": "vf"},
    )

    groups = pipeline.group_evidence((rosc, vf))
    results = pipeline.fuse_evidence_groups(groups)

    assert len(groups) == 1
    assert len(results) == 1
    assert results[0].candidate_event is not None
    assert results[0].candidate_event.status == EventStatus.NEEDS_CONFIRMATION
    assert results[0].uncertainty_reason == "conflicting_evidence"


def test_evidence_order_does_not_change_grouping_or_fusion_result() -> None:
    pipeline = DeterministicMultimodalVoicePipeline()
    speech = _structured_evidence(
        event_type=EventType.SHOCK_DELIVERED,
        confidence=0.86,
    )
    acoustic = _acoustic_shock_evidence(confidence=0.84, seconds=3)

    forward_groups = pipeline.group_evidence((speech, acoustic))
    reverse_groups = pipeline.group_evidence((acoustic, speech))
    forward_result = pipeline.fuse_evidence_groups(forward_groups)[0]
    reverse_result = pipeline.fuse_evidence_groups(reverse_groups)[0]

    assert len(forward_groups) == len(reverse_groups) == 1
    assert {item.id for item in forward_groups[0]} == {item.id for item in reverse_groups[0]}
    assert forward_result.candidate_event is not None
    assert reverse_result.candidate_event is not None
    assert forward_result.candidate_event.event_type == reverse_result.candidate_event.event_type
    assert forward_result.candidate_event.status == reverse_result.candidate_event.status
    assert forward_result.candidate_event.confidence == reverse_result.candidate_event.confidence
    assert set(forward_result.evidence_ids) == set(reverse_result.evidence_ids)
