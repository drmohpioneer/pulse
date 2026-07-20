import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backend.api import routes
from backend.api.main import allowed_origins_from_env
from backend.audio import asr as asr_module
from backend.audio.asr import (
    DEFAULT_ASR_PROMPT,
    DEFAULT_OPENAI_TRANSCRIPTION_MODEL,
    AudioTranscriptionRequest,
    FakeTranscriptionProvider,
    OpenAITranscriptionProvider,
    TranscriptChunkResult,
    TranscriptionProviderRuntimeError,
    TranscriptionProviderConfigurationError,
    _openai_http_transport,
    build_transcription_provider,
    configured_transcription_provider,
)
from backend.services.demo_workflow import (
    DemoConfirmationActionRequest,
    DemoCorrectionActionRequest,
    DemoUndoRequest,
    DemoTranscriptRequest,
    DemoWorkflowSession,
    LiveAudioChunkRequest,
    LiveAudioSessionActionRequest,
    LiveAudioSessionStartRequest,
    LiveAudioUploadRequest,
    LiveTranscriptChunkRequest,
    LiveVoiceSessionActionRequest,
    LiveVoiceSessionStartRequest,
)
from backend.audio.acoustic import FakeAcousticEventProvider
from backend.audio.diarization import FakeDiarizationProvider
from backend.audio.multimodal import AcousticObservationType, TranscriptLanguage
from backend.audio.storage import LiveAudioChunkStore, LiveAudioStoragePolicy
from backend.services.audit_store import DemoAuditStore
from backend.services.confirmation import ConfirmationOption, ConfirmationRequest
from backend.services.evidence_fusion import DeterministicEvidenceFusionEngine
from backend.workflow.events import Evidence, EventSource
from backend.workflow.rhythm import RhythmName
from backend.workflow.events import EventStatus, EventType


def test_high_confidence_exact_rhythm_identification_auto_accepts() -> None:
    session = DemoWorkflowSession()

    result = session.process_transcript(
        DemoTranscriptRequest(text="Rhythm is VF.", confidence=0.95)
    )

    assert result.accepted_event_ids
    assert result.confirmation_requests == []
    assert result.fusion_results[0].candidate_event is not None
    assert result.fusion_results[0].candidate_event.status == EventStatus.ACCEPTED
    assert result.evidence
    assert result.evidence[0].payload["observation_kind"] == "rhythm_identification"
    assert result.state.rhythm_state.current_rhythm == RhythmName.VF
    assert result.state.primary_action == "Deliver shock."
    assert result.state.timeline


def test_medium_confidence_exact_rhythm_identification_auto_accepts() -> None:
    session = DemoWorkflowSession()

    result = session.process_transcript(
        DemoTranscriptRequest(text="Rhythm is VF.", confidence=0.75)
    )

    assert result.accepted_event_ids
    assert result.confirmation_requests == []
    assert result.fusion_results[0].candidate_event is not None
    assert result.fusion_results[0].candidate_event.status == EventStatus.ACCEPTED
    assert result.state.rhythm_state.current_rhythm == RhythmName.VF
    assert result.state.timeline


@pytest.mark.parametrize(
    ("text", "target_event_type"),
    [
        ("not adrenaline", EventType.MEDICATION_GIVEN),
        ("no pulse", EventType.ROSC_ACHIEVED),
        ("no shock", EventType.SHOCK_DELIVERED),
    ],
)
def test_demo_transcript_negative_only_phrases_are_evidence_only(
    text: str,
    target_event_type: EventType,
) -> None:
    session = DemoWorkflowSession()

    result = session.process_transcript(DemoTranscriptRequest(text=text, confidence=1.0))

    assert result.evidence
    assert result.evidence[0].payload["is_positive"] is False
    assert result.evidence[0].payload["observation_kind"] == "correction"
    assert result.confirmation_requests == []
    assert result.accepted_event_ids == []
    assert result.state.timeline == []
    assert len(result.fusion_results) == 1

    fusion_result = result.fusion_results[0]
    assert fusion_result.candidate_event is None
    assert fusion_result.result_kind == "negative_evidence"
    assert fusion_result.is_negative_evidence is True
    assert fusion_result.correction_target_event_type == target_event_type
    assert fusion_result.evidence_ids == [result.evidence[0].id]


def test_demo_scenario_summary_preserves_evidence_only_audit_metadata() -> None:
    session = DemoWorkflowSession()
    response = session.process_transcript(
        DemoTranscriptRequest(text="not adrenaline", confidence=1.0)
    )

    entry = session._scenario_timeline_entry(
        transcript="not adrenaline",
        response=response,
    )

    assert entry.evidence
    assert entry.evidence_ids == [response.evidence[0].id]
    assert entry.result_kind == "negative_evidence"
    assert entry.fusion_decision == "negative_evidence_without_target"
    assert entry.accepted_event is None


def test_demo_accepted_events_ignore_evidence_only_results() -> None:
    session = DemoWorkflowSession()

    result = session.process_transcript(
        DemoTranscriptRequest(text="not adrenaline", confidence=1.0)
    )

    assert result.fusion_results[0].candidate_event is None
    assert result.accepted_event_ids == []
    assert result.state.timeline == []


def test_duplicate_completed_shock_within_dedup_window_counts_once(tmp_path) -> None:
    store = DemoAuditStore(tmp_path / "dedup-audit.jsonl")
    session = DemoWorkflowSession(audit_store=store, session_id="dedup-session")
    first = session.process_transcript(
        DemoTranscriptRequest(
            text="shock delivered",
            confidence=1.0,
            timestamp=datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
        )
    )
    second = session.process_transcript(
        DemoTranscriptRequest(
            text="shock delivered",
            confidence=1.0,
            timestamp=datetime(2026, 7, 20, 12, 0, 3, tzinfo=UTC),
        )
    )

    assert first.state.shock_count == 1
    assert second.state.shock_count == 1
    assert second.fusion_results[-1].result_kind == "deduplicated"
    assert any(record["record_type"] == "voice_event_deduplicated" for record in store.records("dedup-session"))


def test_completed_medication_supersedes_matching_pending_command() -> None:
    session = DemoWorkflowSession()
    command = session.process_transcript(
        DemoTranscriptRequest(text="give 1 mg epinephrine", confidence=1.0)
    )
    completed = session.process_transcript(
        DemoTranscriptRequest(text="epinephrine is in", confidence=1.0)
    )

    assert len(command.confirmation_requests) == 1
    assert completed.accepted_event_ids
    assert completed.confirmation_requests == []
    assert completed.state.medication_state.epinephrine_count == 1
    assert len(completed.state.timeline) == 1


def test_completed_closed_loop_statements_advance_state_without_confirmations() -> None:
    session = DemoWorkflowSession()

    started = session.process_transcript(DemoTranscriptRequest(text="cpr started", confidence=1.0))
    rhythm = session.process_transcript(DemoTranscriptRequest(text="rhythm is vf", confidence=1.0))
    shocked = session.process_transcript(DemoTranscriptRequest(text="shock delivered", confidence=1.0))
    resumed = session.process_transcript(DemoTranscriptRequest(text="resume cpr", confidence=1.0))

    for response in (started, rhythm, shocked, resumed):
        assert response.confirmation_requests == []
    assert started.state.cpr_status == "Active"
    assert rhythm.state.current_rhythm == "VF"
    assert rhythm.state.primary_action == "Deliver shock."
    assert shocked.state.shock_count == 1
    assert shocked.undoable_event_ids
    assert shocked.state.undoable_event_ids == shocked.undoable_event_ids
    assert resumed.state.cpr_status == "Active"


def test_undo_auto_accepted_event_rebuilds_state_and_audits(tmp_path) -> None:
    store = DemoAuditStore(tmp_path / "undo-audit.jsonl")
    session = DemoWorkflowSession(audit_store=store, session_id="undo-session")
    accepted = session.process_transcript(
        DemoTranscriptRequest(text="patient is vf", confidence=1.0)
    )
    event_id = accepted.accepted_event_ids[0]

    undone = session.undo_auto_accepted_event(DemoUndoRequest(event_id=event_id))
    replayed = session.replay_persisted_timeline("undo-session")

    assert undone.state.rhythm_state.current_rhythm == RhythmName.UNKNOWN
    assert replayed.rhythm_state.current_rhythm == RhythmName.UNKNOWN
    assert event_id not in undone.undoable_event_ids
    assert any(record["record_type"] == "auto_accepted_event_undone" for record in store.records("undo-session"))


def test_shock_recorded_during_asystole_adds_protocol_deviation_safety_flag() -> None:
    session = DemoWorkflowSession()
    session.process_transcript(DemoTranscriptRequest(text="rhythm is asystole", confidence=1.0))
    response = session.process_transcript(DemoTranscriptRequest(text="shock delivered", confidence=1.0))

    assert response.state.shock_count == 1
    assert "Shock recorded with last rhythm asystole — not indicated" in response.state.safety_flags


@pytest.mark.parametrize("text", ["not adrenaline", "epi is in 1 mg IV"])
def test_demo_transcript_response_serializes_voice_evidence_payload(
    text: str,
) -> None:
    result = DemoWorkflowSession().process_transcript(
        DemoTranscriptRequest(text=text, confidence=1.0)
    )

    assert result.model_dump_json()


def test_confirm_high_impact_candidate_updates_engine_and_timeline() -> None:
    session = DemoWorkflowSession()
    result = session.process_transcript(
        DemoTranscriptRequest(text="give epinephrine", confidence=1.0)
    )
    request = result.confirmation_requests[0]

    confirmed = session.confirm_voice_candidate(
        DemoConfirmationActionRequest(confirmation_request_id=request.id)
    )

    event = confirmed.fusion_results[0].candidate_event
    assert event is not None
    assert event.status == EventStatus.ACCEPTED
    assert confirmed.accepted_event_ids == [request.candidate_event_id]
    assert confirmed.confirmation_requests == []
    assert confirmed.state.medication_state.epinephrine_count == 1
    assert confirmed.state.timeline[0].id == request.candidate_event_id


def test_reject_high_impact_candidate_does_not_update_engine() -> None:
    session = DemoWorkflowSession()
    result = session.process_transcript(
        DemoTranscriptRequest(text="give epinephrine", confidence=1.0)
    )
    request = result.confirmation_requests[0]

    rejected = session.reject_voice_candidate(
        DemoConfirmationActionRequest(candidate_event_id=request.candidate_event_id)
    )

    event = rejected.fusion_results[0].candidate_event
    assert event is not None
    assert event.status == EventStatus.REJECTED
    assert rejected.accepted_event_ids == []
    assert rejected.confirmation_requests == []
    assert rejected.state.medication_state.epinephrine_count == 0
    assert rejected.state.timeline == []
    assert rejected.evidence


def test_negative_evidence_only_cannot_be_confirmed_as_positive() -> None:
    session = DemoWorkflowSession()
    result = session.process_transcript(
        DemoTranscriptRequest(text="not adrenaline", confidence=1.0)
    )

    assert result.fusion_results[0].candidate_event is None
    assert result.confirmation_requests == []
    with pytest.raises(ValueError, match="unknown or already resolved"):
        session.confirm_voice_candidate(
            DemoConfirmationActionRequest(candidate_event_id="not-a-candidate")
        )
    assert session.current_state().timeline == []


def test_command_candidate_can_be_confirmed_by_human() -> None:
    session = DemoWorkflowSession()
    result = session.process_transcript(
        DemoTranscriptRequest(text="give epi 1 mg", confidence=1.0)
    )
    request = result.confirmation_requests[0]

    confirmed = session.confirm_voice_candidate(
        DemoConfirmationActionRequest(confirmation_request_id=request.id)
    )

    event = confirmed.fusion_results[0].candidate_event
    assert event is not None
    assert event.status == EventStatus.ACCEPTED
    assert confirmed.accepted_event_ids == [request.candidate_event_id]
    assert confirmed.state.medication_state.epinephrine_count == 1


def test_rejected_candidate_remains_auditable_in_voice_review() -> None:
    session = DemoWorkflowSession()
    result = session.process_transcript(
        DemoTranscriptRequest(text="give epinephrine", confidence=1.0)
    )
    request = result.confirmation_requests[0]

    rejected = session.reject_voice_candidate(
        DemoConfirmationActionRequest(confirmation_request_id=request.id)
    )

    event = rejected.fusion_results[0].candidate_event
    assert event is not None
    assert event.status == EventStatus.REJECTED
    assert rejected.fusion_results[0].evidence_ids == [rejected.evidence[0].id]
    assert rejected.evidence[0].payload["payload"] == {"medication": "epinephrine"}
    assert rejected.state.medication_state.epinephrine_count == 0


def test_live_voice_session_start_and_stop() -> None:
    session = DemoWorkflowSession()

    started = session.start_live_voice_session(LiveVoiceSessionStartRequest())
    stopped = session.stop_live_voice_session(
        LiveVoiceSessionActionRequest(session_id=started.session.session_id)
    )

    assert started.session.session_id == "demo-live-1"
    assert started.session.active is True
    assert started.session.next_sequence == 1
    assert stopped.session.active is False
    assert stopped.session.stopped_at is not None


def test_live_transcript_chunk_creates_expected_fusion_result() -> None:
    session = DemoWorkflowSession()
    live = session.start_live_voice_session(LiveVoiceSessionStartRequest())

    response = session.ingest_live_transcript_chunk(
        LiveTranscriptChunkRequest(
            session_id=live.session.session_id,
            sequence=1,
            text="Rhythm is VF.",
            confidence=1.0,
            speaker_label="team_leader",
            language=TranscriptLanguage.ENGLISH,
        )
    )

    event = response.result.fusion_results[0].candidate_event
    assert response.chunk.sequence == 1
    assert response.session.next_sequence == 2
    assert event is not None
    assert event.event_type == EventType.RHYTHM_CHECKED
    assert event.status == EventStatus.ACCEPTED
    assert response.result.confirmation_requests == []


def test_scripted_live_stream_produces_ordered_multilingual_chunks() -> None:
    session = DemoWorkflowSession()
    live = session.start_live_voice_session(LiveVoiceSessionStartRequest())
    chunks = []
    decisions = []

    for _ in range(5):
        response = session.advance_scripted_live_stream(
            LiveVoiceSessionActionRequest(session_id=live.session.session_id)
        )
        assert response.chunk is not None
        assert response.result is not None
        chunks.append(response.chunk)
        decisions.append(response.result.fusion_results[-1])

    complete = session.advance_scripted_live_stream(
        LiveVoiceSessionActionRequest(session_id=live.session.session_id)
    )

    assert [chunk.sequence for chunk in chunks] == [1, 2, 3, 4, 5]
    assert chunks[0].language == TranscriptLanguage.EGYPTIAN_ARABIC
    assert chunks[4].language == TranscriptLanguage.MIXED
    assert decisions[0].candidate_event is not None
    assert decisions[0].candidate_event.payload["medication"] == "epinephrine"
    assert decisions[2].candidate_event is None
    assert decisions[2].result_kind == "negative_evidence"
    assert complete.is_complete is True


def test_live_negative_evidence_creates_audit_result_only() -> None:
    session = DemoWorkflowSession()
    live = session.start_live_voice_session(LiveVoiceSessionStartRequest())

    response = session.ingest_live_transcript_chunk(
        LiveTranscriptChunkRequest(
            session_id=live.session.session_id,
            sequence=1,
            text="no pulse",
            confidence=1.0,
        )
    )

    result = response.result.fusion_results[0]
    assert result.candidate_event is None
    assert result.result_kind == "negative_evidence"
    assert response.result.confirmation_requests == []
    assert response.result.accepted_event_ids == []


def test_confirmed_live_stream_candidate_updates_engine() -> None:
    session = DemoWorkflowSession()
    live = session.start_live_voice_session(LiveVoiceSessionStartRequest())
    response = session.ingest_live_transcript_chunk(
        LiveTranscriptChunkRequest(
            session_id=live.session.session_id,
            sequence=1,
            text="give epinephrine",
            confidence=1.0,
        )
    )
    request = response.result.confirmation_requests[0]

    confirmed = session.confirm_voice_candidate(
        DemoConfirmationActionRequest(confirmation_request_id=request.id)
    )

    assert confirmed.accepted_event_ids == [request.candidate_event_id]
    assert confirmed.state.medication_state.epinephrine_count == 1
    assert len(confirmed.state.timeline) == 1


def test_rejected_live_stream_candidate_does_not_update_engine() -> None:
    session = DemoWorkflowSession()
    live = session.start_live_voice_session(LiveVoiceSessionStartRequest())
    response = session.ingest_live_transcript_chunk(
        LiveTranscriptChunkRequest(
            session_id=live.session.session_id,
            sequence=1,
            text="give epinephrine",
            confidence=1.0,
        )
    )
    request = response.result.confirmation_requests[0]

    rejected = session.reject_voice_candidate(
        DemoConfirmationActionRequest(confirmation_request_id=request.id)
    )

    assert rejected.accepted_event_ids == []
    assert rejected.state.medication_state.epinephrine_count == 0
    assert rejected.state.timeline == []


def test_live_audio_session_start_and_stop_with_fake_asr() -> None:
    session = DemoWorkflowSession(transcription_provider=FakeTranscriptionProvider())

    started = session.start_live_audio_session(LiveAudioSessionStartRequest())
    stopped = session.stop_live_audio_session(
        LiveAudioSessionActionRequest(session_id=started.session.session_id)
    )

    assert started.session.session_id == "demo-audio-1"
    assert started.session.provider_name == "fake"
    assert started.session.active is True
    assert stopped.session.active is False
    assert stopped.session.stopped_at is not None


def test_fake_asr_audio_chunk_flows_into_multimodal_pipeline() -> None:
    session = DemoWorkflowSession(transcription_provider=FakeTranscriptionProvider())
    live = session.start_live_audio_session(LiveAudioSessionStartRequest())

    response = session.ingest_live_audio_chunk(
        LiveAudioChunkRequest(
            session_id=live.session.session_id,
            sequence=1,
            audio_reference="memory://chunk-1",
            content_type="audio/webm",
            duration_ms=1000,
            metadata={
                "simulated_text": "Rhythm is VF.",
                "language": TranscriptLanguage.ENGLISH.value,
                "speaker_label": "team_leader",
            },
        )
    )

    event = response.result.fusion_results[0].candidate_event
    assert response.transcript.text == "Rhythm is VF."
    assert response.transcript.provider_name == "fake"
    assert response.session.next_sequence == 2
    assert event is not None
    assert event.event_type == EventType.RHYTHM_CHECKED
    assert event.status == EventStatus.ACCEPTED
    assert response.result.confirmation_requests == []


def test_live_audio_completed_action_auto_accepts() -> None:
    session = DemoWorkflowSession(transcription_provider=FakeTranscriptionProvider())
    live = session.start_live_audio_session(LiveAudioSessionStartRequest())

    response = session.ingest_live_audio_chunk(
        LiveAudioChunkRequest(
            session_id=live.session.session_id,
            sequence=1,
            audio_reference="memory://chunk-epi",
            metadata={
                "simulated_text": "epi is in 1 mg IV",
                "language": TranscriptLanguage.ENGLISH.value,
            },
        )
    )

    event = response.result.fusion_results[0].candidate_event
    assert event is not None
    assert event.event_type == EventType.MEDICATION_GIVEN
    assert event.status == EventStatus.ACCEPTED
    assert response.result.accepted_event_ids == [str(event.id)]
    assert response.result.state.medication_state.epinephrine_count == 1


def test_live_audio_negative_phrase_is_evidence_only() -> None:
    session = DemoWorkflowSession(transcription_provider=FakeTranscriptionProvider())
    live = session.start_live_audio_session(LiveAudioSessionStartRequest())

    response = session.ingest_live_audio_chunk(
        LiveAudioChunkRequest(
            session_id=live.session.session_id,
            sequence=1,
            audio_reference="memory://chunk-negative",
            metadata={
                "simulated_text": "no shock",
                "language": TranscriptLanguage.ENGLISH.value,
            },
        )
    )

    result = response.result.fusion_results[0]
    assert result.candidate_event is None
    assert result.result_kind == "negative_evidence"
    assert result.is_negative_evidence is True
    assert response.result.confirmation_requests == []
    assert response.result.accepted_event_ids == []


def test_confirmed_live_audio_candidate_updates_deterministic_engine() -> None:
    session = DemoWorkflowSession(transcription_provider=FakeTranscriptionProvider())
    live = session.start_live_audio_session(LiveAudioSessionStartRequest())
    response = session.ingest_live_audio_chunk(
        LiveAudioChunkRequest(
            session_id=live.session.session_id,
            sequence=1,
            audio_reference="memory://chunk-rhythm",
            metadata={
                "simulated_text": "give epinephrine",
                "language": TranscriptLanguage.ENGLISH.value,
            },
        )
    )
    request = response.result.confirmation_requests[0]

    confirmed = session.confirm_voice_candidate(
        DemoConfirmationActionRequest(confirmation_request_id=request.id)
    )

    assert confirmed.accepted_event_ids == [request.candidate_event_id]
    assert confirmed.state.medication_state.epinephrine_count == 1
    assert len(confirmed.state.timeline) == 1


def test_rejected_live_audio_candidate_does_not_update_engine() -> None:
    session = DemoWorkflowSession(transcription_provider=FakeTranscriptionProvider())
    live = session.start_live_audio_session(LiveAudioSessionStartRequest())
    response = session.ingest_live_audio_chunk(
        LiveAudioChunkRequest(
            session_id=live.session.session_id,
            sequence=1,
            audio_reference="memory://chunk-rhythm",
            metadata={
                "simulated_text": "give epinephrine",
                "language": TranscriptLanguage.ENGLISH.value,
            },
        )
    )
    request = response.result.confirmation_requests[0]

    rejected = session.reject_voice_candidate(
        DemoConfirmationActionRequest(confirmation_request_id=request.id)
    )

    assert rejected.accepted_event_ids == []
    assert rejected.state.medication_state.epinephrine_count == 0
    assert rejected.state.timeline == []


def test_missing_remote_asr_provider_config_fails_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PULSE_ASR_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(TranscriptionProviderConfigurationError):
        build_transcription_provider()


def test_configured_missing_real_asr_falls_back_to_fake_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PULSE_ASR_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = configured_transcription_provider()
    session = DemoWorkflowSession(transcription_provider=provider)

    started = session.start_live_audio_session(LiveAudioSessionStartRequest())
    response = session.ingest_live_audio_chunk(
        LiveAudioChunkRequest(
            session_id=started.session.session_id,
            sequence=1,
            audio_reference="memory://fallback",
            metadata={"simulated_text": "Rhythm is VF.", "language": "en"},
        )
    )

    assert started.session.provider_name == "openai"
    assert started.session.provider_mode == "provider_error_fallback"
    assert started.session.fallback_provider_name == "fake"
    assert response.transcript is not None
    assert response.transcript.provider_name == "fake"
    assert response.transcript.provider_metadata["configured_provider"] == "openai"


def test_allowed_origins_default_and_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PULSE_ALLOWED_ORIGINS", raising=False)
    assert allowed_origins_from_env() == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    monkeypatch.setenv(
        "PULSE_ALLOWED_ORIGINS",
        "https://pulse.example.com, http://localhost:3000 ",
    )

    assert allowed_origins_from_env() == [
        "https://pulse.example.com",
        "http://localhost:3000",
    ]


def test_openai_asr_default_model_language_and_prompt_are_sent(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PULSE_OPENAI_TRANSCRIPTION_MODEL", raising=False)
    monkeypatch.delenv("PULSE_ASR_LANGUAGE", raising=False)
    monkeypatch.delenv("PULSE_ASR_PROMPT", raising=False)
    audio_path = tmp_path / "chunk.webm"
    audio_path.write_bytes(b"fake audio bytes")
    captured: dict[str, object] = {}

    def transport(
        request,
        audio_bytes,
        filename,
        model,
        url,
        api_key,
        timeout_seconds,
        language,
        prompt,
    ):
        captured.update(
            {
                "model": model,
                "language": language,
                "prompt": prompt,
                "audio_bytes": audio_bytes,
                "filename": filename,
            }
        )
        return {"text": "Rhythm VF.", "language": "en"}

    provider = OpenAITranscriptionProvider(api_key="test-key", transport=transport)

    provider.transcribe(
        AudioTranscriptionRequest(
            session_id="openai-fields",
            sequence=1,
            audio_reference=str(audio_path),
            content_type="audio/webm",
        )
    )

    assert captured["model"] == DEFAULT_OPENAI_TRANSCRIPTION_MODEL
    assert captured["language"] == "en"
    assert captured["prompt"] == DEFAULT_ASR_PROMPT
    assert captured["audio_bytes"] == b"fake audio bytes"
    assert captured["filename"] == "chunk.webm"


def test_openai_asr_language_and_prompt_env_overrides_are_sent(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PULSE_ASR_LANGUAGE", "ar")
    monkeypatch.setenv("PULSE_ASR_PROMPT", "Egyptian Arabic resuscitation vocabulary")
    audio_path = tmp_path / "chunk.webm"
    audio_path.write_bytes(b"fake audio bytes")
    captured: dict[str, object] = {}

    def transport(
        request,
        audio_bytes,
        filename,
        model,
        url,
        api_key,
        timeout_seconds,
        language,
        prompt,
    ):
        captured["language"] = language
        captured["prompt"] = prompt
        return {"text": "Rhythm VF.", "language": "en"}

    provider = OpenAITranscriptionProvider(api_key="test-key", transport=transport)
    provider.transcribe(
        AudioTranscriptionRequest(
            session_id="openai-overrides",
            sequence=1,
            audio_reference=str(audio_path),
            content_type="audio/webm",
        )
    )

    assert captured["language"] == "ar"
    assert captured["prompt"] == "Egyptian Arabic resuscitation vocabulary"


def test_openai_http_transport_includes_language_and_prompt_in_multipart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"text":"Rhythm VF."}'

    def fake_urlopen(request, timeout):
        captured["body"] = request.data
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(asr_module.urllib.request, "urlopen", fake_urlopen)

    _openai_http_transport(
        AudioTranscriptionRequest(
            session_id="multipart",
            sequence=1,
            audio_reference="chunk.webm",
            content_type="audio/webm",
        ),
        b"fake audio",
        "chunk.webm",
        "gpt-4o-transcribe",
        "https://example.test/audio/transcriptions",
        "test-key",
        7.5,
        "en",
        "Cardiac arrest vocabulary",
    )

    body = captured["body"]
    assert isinstance(body, bytes)
    assert b'name="model"\r\n\r\ngpt-4o-transcribe\r\n' in body
    assert b'name="language"\r\n\r\nen\r\n' in body
    assert b'name="prompt"\r\n\r\nCardiac arrest vocabulary\r\n' in body
    assert b'name="response_format"\r\n\r\njson\r\n' in body
    assert captured["timeout"] == 7.5


def test_openai_asr_prompt_stays_static_across_segments(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PULSE_ASR_PROMPT", "Resus vocabulary")
    audio_path = tmp_path / "chunk.webm"
    audio_path.write_bytes(b"fake audio bytes")
    prompts: list[str] = []
    transcript_texts = [
        "The rhythm is VF.",
        "Shock delivered.",
    ]

    def transport(
        request,
        audio_bytes,
        filename,
        model,
        url,
        api_key,
        timeout_seconds,
        language,
        prompt,
    ):
        prompts.append(prompt)
        return {"text": transcript_texts.pop(0), "language": "en"}

    provider = OpenAITranscriptionProvider(api_key="test-key", transport=transport)
    request = AudioTranscriptionRequest(
        session_id="same-session",
        sequence=1,
        audio_reference=str(audio_path),
        content_type="audio/webm",
    )

    provider.transcribe(request)
    provider.transcribe(request.model_copy(update={"sequence": 2}))

    assert prompts[0] == "Resus vocabulary"
    assert prompts[1] == "Resus vocabulary"
    assert all("Previous transcript context" not in prompt for prompt in prompts)


def test_openai_asr_empty_transcript_still_raises_runtime_error(tmp_path) -> None:
    audio_path = tmp_path / "chunk.webm"
    audio_path.write_bytes(b"fake audio bytes")

    def transport(
        request,
        audio_bytes,
        filename,
        model,
        url,
        api_key,
        timeout_seconds,
        language,
        prompt,
    ):
        return {"text": " "}

    provider = OpenAITranscriptionProvider(api_key="test-key", transport=transport)

    with pytest.raises(
        TranscriptionProviderRuntimeError,
        match="OpenAI ASR returned an empty transcript",
    ):
        provider.transcribe(
            AudioTranscriptionRequest(
                session_id="empty-transcript",
                sequence=1,
                audio_reference=str(audio_path),
                content_type="audio/webm",
            )
        )


def test_mocked_real_asr_success_flows_transcript_into_multimodal_pipeline(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PULSE_ASR_LANGUAGE", raising=False)
    monkeypatch.delenv("PULSE_ASR_PROMPT", raising=False)
    audio_path = tmp_path / "chunk.webm"
    audio_path.write_bytes(b"fake audio bytes")
    store = DemoAuditStore(tmp_path / "pulse-audit.jsonl")

    def transport(
        request,
        audio_bytes,
        filename,
        model,
        url,
        api_key,
        timeout_seconds,
        language,
        prompt,
    ):
        assert audio_bytes == b"fake audio bytes"
        assert filename == "chunk.webm"
        assert model == "test-transcribe"
        assert api_key == "test-key"
        assert language == "en"
        assert prompt == DEFAULT_ASR_PROMPT
        return {"text": "Rhythm is VF.", "language": "en", "confidence": 0.99}

    provider = OpenAITranscriptionProvider(
        api_key="test-key",
        model="test-transcribe",
        transport=transport,
    )
    session = DemoWorkflowSession(
        transcription_provider=provider,
        audit_store=store,
        session_id="openai-success",
    )
    live = session.start_live_audio_session(LiveAudioSessionStartRequest())

    response = session.ingest_live_audio_chunk(
        LiveAudioChunkRequest(
            session_id=live.session.session_id,
            sequence=1,
            audio_reference=str(audio_path),
            content_type="audio/webm",
        )
    )

    event = response.result.fusion_results[0].candidate_event
    assert response.transcript is not None
    assert response.transcript.provider_name == "openai"
    assert response.transcript.provider_metadata["provider_mode"] == "configured_real_provider"
    assert event is not None
    assert event.event_type == EventType.RHYTHM_CHECKED
    assert event.status == EventStatus.ACCEPTED
    assert response.result.accepted_event_ids == [str(event.id)]
    assert response.result.state.timeline
    audio_records = [
        record
        for record in store.records("openai-success")
        if record["record_type"] == "audio_chunk_ingested"
    ]
    assert audio_records[0]["payload"]["transcript"]["provider_name"] == "openai"
    assert (
        audio_records[0]["payload"]["provider_status"]["mode"]
        == "configured_real_provider"
    )


class _FailingTranscriptionProvider:
    provider_name = "mock_real"

    def status(self):
        from backend.audio.asr import TranscriptionProviderStatus

        return TranscriptionProviderStatus(
            provider_name=self.provider_name,
            mode="configured_real_provider",
            configured=True,
            available=True,
        )

    def transcribe(self, request):
        raise TranscriptionProviderRuntimeError("mock provider failed")


class _PathCapturingTranscriptionProvider:
    provider_name = "mock_real"

    def __init__(self) -> None:
        self.references: list[str] = []

    def transcribe(self, request):
        path = Path(request.audio_reference)
        assert path.exists()
        self.references.append(request.audio_reference)
        timestamp = request.timestamp or datetime.now(UTC)
        return TranscriptChunkResult(
            session_id=request.session_id,
            sequence=request.sequence,
            text="Rhythm is VF.",
            confidence=0.99,
            started_at=timestamp,
            ended_at=timestamp + timedelta(seconds=1),
            language=TranscriptLanguage.ENGLISH,
            provider_name=self.provider_name,
            audio_reference=request.audio_reference,
            provider_metadata={"mock_real": True},
        )


def test_mocked_real_asr_error_returns_safe_result_and_does_not_mutate_engine(tmp_path) -> None:
    store = DemoAuditStore(tmp_path / "pulse-audit.jsonl")
    session = DemoWorkflowSession(
        transcription_provider=_FailingTranscriptionProvider(),
        audit_store=store,
        session_id="asr-error",
    )
    live = session.start_live_audio_session(LiveAudioSessionStartRequest())

    response = session.ingest_live_audio_chunk(
        LiveAudioChunkRequest(
            session_id=live.session.session_id,
            sequence=1,
            audio_reference="memory://bad-audio",
        )
    )

    records = store.records("asr-error")
    assert response.transcript is None
    assert response.result is None
    assert response.transcription_error == "mock provider failed"
    assert response.session.next_sequence == 2
    assert session.current_state().timeline == []
    assert any(
        record["record_type"] == "audio_chunk_ingested"
        and record["payload"]["transcription_error"] == "mock provider failed"
        for record in records
    )


def test_valid_audio_upload_stores_file_and_passes_server_reference_to_asr(tmp_path) -> None:
    provider = _PathCapturingTranscriptionProvider()
    store = DemoAuditStore(tmp_path / "pulse-audit.jsonl")
    session = DemoWorkflowSession(
        transcription_provider=provider,
        audio_chunk_store=LiveAudioChunkStore(root=tmp_path / "audio"),
        audit_store=store,
        session_id="upload-session",
    )
    live = session.start_live_audio_session(LiveAudioSessionStartRequest())

    response = session.ingest_live_audio_upload(
        LiveAudioUploadRequest(
            session_id=live.session.session_id,
            sequence=1,
            content_type="audio/webm",
            content=b"webm bytes",
        )
    )

    event = response.result.fusion_results[0].candidate_event
    assert provider.references
    assert Path(provider.references[0]).exists()
    assert response.audio_chunk.audio_reference == provider.references[0]
    assert response.audio_chunk.metadata["file_size_bytes"] == len(b"webm bytes")
    assert event is not None
    assert event.event_type == EventType.RHYTHM_CHECKED
    assert event.status == EventStatus.ACCEPTED
    records = store.records("upload-session")
    audio_record = next(record for record in records if record["record_type"] == "audio_chunk_ingested")
    assert audio_record["payload"]["chunk"]["metadata"]["storage_reference"] == provider.references[0]
    assert audio_record["payload"]["transcript"]["provider_metadata"]["mock_real"] is True


def test_audio_upload_rejects_invalid_content_type(tmp_path) -> None:
    session = DemoWorkflowSession(
        audio_chunk_store=LiveAudioChunkStore(root=tmp_path / "audio"),
    )
    live = session.start_live_audio_session(LiveAudioSessionStartRequest())

    with pytest.raises(ValueError, match="unsupported audio content type"):
        session.ingest_live_audio_upload(
            LiveAudioUploadRequest(
                session_id=live.session.session_id,
                sequence=1,
                content_type="text/plain",
                content=b"not audio",
            )
        )


@pytest.mark.parametrize(
    "content_type",
    [
        "audio/mp4",
        "audio/m4a",
    ],
)
def test_audio_upload_accepts_safari_audio_with_m4a_suffix(
    tmp_path,
    content_type: str,
) -> None:
    store = LiveAudioChunkStore(root=tmp_path / "audio")

    stored = store.store(
        session_id="safari-session",
        sequence=1,
        content=b"safari audio",
        content_type=content_type,
    )

    assert stored.content_type == content_type
    assert stored.storage_reference.endswith(".m4a")
    assert Path(stored.storage_reference).exists()


def test_audio_upload_rejects_oversized_chunk(tmp_path) -> None:
    session = DemoWorkflowSession(
        audio_chunk_store=LiveAudioChunkStore(
            root=tmp_path / "audio",
            policy=LiveAudioStoragePolicy(max_chunk_bytes=3),
        ),
    )
    live = session.start_live_audio_session(LiveAudioSessionStartRequest())

    with pytest.raises(ValueError, match="exceeds maximum size"):
        session.ingest_live_audio_upload(
            LiveAudioUploadRequest(
                session_id=live.session.session_id,
                sequence=1,
                content_type="audio/webm",
                content=b"four",
            )
        )


def test_audio_upload_rejects_inactive_or_missing_session(tmp_path) -> None:
    session = DemoWorkflowSession(
        audio_chunk_store=LiveAudioChunkStore(root=tmp_path / "audio"),
    )
    live = session.start_live_audio_session(LiveAudioSessionStartRequest())
    session.stop_live_audio_session(
        LiveAudioSessionActionRequest(session_id=live.session.session_id)
    )

    with pytest.raises(ValueError, match="stopped"):
        session.ingest_live_audio_upload(
            LiveAudioUploadRequest(
                session_id=live.session.session_id,
                sequence=1,
                content_type="audio/webm",
                content=b"bytes",
            )
        )
    with pytest.raises(ValueError, match="unknown live audio session"):
        session.ingest_live_audio_upload(
            LiveAudioUploadRequest(
                session_id="missing-session",
                sequence=1,
                content_type="audio/webm",
                content=b"bytes",
            )
        )


def test_audio_upload_asr_failure_persists_error_without_engine_mutation(tmp_path) -> None:
    store = DemoAuditStore(tmp_path / "pulse-audit.jsonl")
    session = DemoWorkflowSession(
        transcription_provider=_FailingTranscriptionProvider(),
        audio_chunk_store=LiveAudioChunkStore(root=tmp_path / "audio"),
        audit_store=store,
        session_id="upload-error",
    )
    live = session.start_live_audio_session(LiveAudioSessionStartRequest())

    response = session.ingest_live_audio_upload(
        LiveAudioUploadRequest(
            session_id=live.session.session_id,
            sequence=1,
            content_type="audio/webm",
            content=b"bad bytes",
        )
    )

    assert response.transcription_error == "mock provider failed"
    assert response.result is None
    assert response.session.next_sequence == 2
    assert session.current_state().timeline == []
    records = store.records("upload-error")
    audio_record = next(record for record in records if record["record_type"] == "audio_chunk_ingested")
    assert audio_record["payload"]["chunk"]["metadata"]["storage_reference"]
    assert audio_record["payload"]["transcription_error"] == "mock provider failed"


def test_audio_storage_cleanup_removes_expired_chunks(tmp_path) -> None:
    store = LiveAudioChunkStore(
        root=tmp_path / "audio",
        policy=LiveAudioStoragePolicy(retention_seconds=1),
    )
    stored = store.store(
        session_id="cleanup-session",
        sequence=1,
        content=b"bytes",
        content_type="audio/webm",
    )

    removed = store.cleanup_expired(now=datetime.now(UTC) + timedelta(seconds=5))

    assert removed == 1
    assert not Path(stored.storage_reference).exists()


class _Upload:
    content_type = "audio/webm"

    async def read(self) -> bytes:
        return b"webm bytes"


def test_audio_upload_endpoint_accepts_multipart_and_uses_stored_reference(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _PathCapturingTranscriptionProvider()
    session = DemoWorkflowSession(
        transcription_provider=provider,
        audio_chunk_store=LiveAudioChunkStore(root=tmp_path / "audio"),
    )
    monkeypatch.setattr(routes, "demo_session", session)
    started = session.start_live_audio_session(LiveAudioSessionStartRequest())

    response = asyncio.run(
        routes.upload_live_audio_chunk(
            session_id=started.session.session_id,
            sequence=1,
            timestamp=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
            content_type="audio/webm",
            audio=_Upload(),
        )
    )

    assert provider.references
    assert response.audio_chunk.audio_reference == provider.references[0]
    assert response.audio_chunk.metadata["file_size_bytes"] == len(b"webm bytes")


def test_fake_diarization_adds_speaker_and_role_metadata_to_evidence() -> None:
    session = DemoWorkflowSession(
        transcription_provider=FakeTranscriptionProvider(),
        diarization_provider=FakeDiarizationProvider(),
        acoustic_event_provider=FakeAcousticEventProvider(),
    )
    live = session.start_live_audio_session(LiveAudioSessionStartRequest())

    response = session.ingest_live_audio_chunk(
        LiveAudioChunkRequest(
            session_id=live.session.session_id,
            sequence=1,
            audio_reference="memory://speaker-role",
            metadata={
                "simulated_text": "Rhythm is VF.",
                "language": TranscriptLanguage.ENGLISH.value,
                "speaker_label": "speaker_1",
                "speaker_role": "team_leader",
                "role_confidence": 0.77,
                "diarization_confidence": 0.88,
            },
        )
    )

    evidence = response.result.evidence[0]
    assert response.diarization.turns[0].speaker_id == "speaker_1"
    assert response.diarization.turns[0].role_hypothesis is not None
    assert evidence.payload["speaker_id"] == "speaker_1"
    assert evidence.payload["role"] == "team_leader"
    assert evidence.payload["role_confidence"] == 0.77


def test_role_metadata_is_advisory_and_does_not_change_acceptance_policy() -> None:
    session = DemoWorkflowSession(
        transcription_provider=FakeTranscriptionProvider(),
        diarization_provider=FakeDiarizationProvider(),
        acoustic_event_provider=FakeAcousticEventProvider(),
    )
    live = session.start_live_audio_session(LiveAudioSessionStartRequest())

    response = session.ingest_live_audio_chunk(
        LiveAudioChunkRequest(
            session_id=live.session.session_id,
            sequence=1,
            audio_reference="memory://role-advisory",
            metadata={
                "simulated_text": "epi is in 1 mg IV",
                "language": TranscriptLanguage.ENGLISH.value,
                "speaker_label": "speaker_lead",
                "speaker_role": "team_leader",
                "role_confidence": 1.0,
            },
        )
    )

    event = response.result.fusion_results[0].candidate_event
    assert event is not None
    assert event.event_type == EventType.MEDICATION_GIVEN
    assert event.status == EventStatus.ACCEPTED
    assert response.result.accepted_event_ids == [str(event.id)]


def test_fake_acoustic_defib_discharge_corroborates_shock_from_allowed_source() -> None:
    session = DemoWorkflowSession(
        transcription_provider=FakeTranscriptionProvider(),
        diarization_provider=FakeDiarizationProvider(),
        acoustic_event_provider=FakeAcousticEventProvider(),
    )
    live = session.start_live_audio_session(LiveAudioSessionStartRequest())

    response = session.ingest_live_audio_chunk(
        LiveAudioChunkRequest(
            session_id=live.session.session_id,
            sequence=1,
            audio_reference="memory://shock-acoustic",
            metadata={
                "simulated_text": "shock delivered",
                "language": TranscriptLanguage.ENGLISH.value,
                "acoustic_observation_type": AcousticObservationType.DEFIBRILLATOR_DISCHARGE.value,
                "acoustic_confidence": 0.9,
            },
        )
    )

    result = response.result.fusion_results[0]
    event = result.candidate_event
    assert response.acoustic_events.detections[0].acoustic_type == AcousticObservationType.DEFIBRILLATOR_DISCHARGE
    assert len(response.result.evidence) == 2
    assert event is not None
    assert event.event_type == EventType.SHOCK_DELIVERED
    assert event.status == EventStatus.ACCEPTED
    assert response.result.accepted_event_ids == [str(event.id)]


def test_wrong_source_acoustic_label_does_not_corroborate_shock() -> None:
    evidence = Evidence(
        source=EventSource.SPEECH,
        evidence_type="defibrillator_discharge",
        confidence=1.0,
        payload={"observation_type": "defibrillator_discharge"},
        raw_reference="defibrillator_discharge",
    )

    result = DeterministicEvidenceFusionEngine().fuse([evidence])

    assert result.candidate_event is None
    assert result.result_kind == "no_clinical_interpretation"


def test_diarization_and_acoustic_metadata_persist_in_audit_store(tmp_path) -> None:
    store = DemoAuditStore(tmp_path / "pulse-audit.jsonl")
    session = DemoWorkflowSession(
        transcription_provider=FakeTranscriptionProvider(),
        diarization_provider=FakeDiarizationProvider(),
        acoustic_event_provider=FakeAcousticEventProvider(),
        audit_store=store,
        session_id="metadata-audit",
    )
    live = session.start_live_audio_session(LiveAudioSessionStartRequest())

    session.ingest_live_audio_chunk(
        LiveAudioChunkRequest(
            session_id=live.session.session_id,
            sequence=1,
            audio_reference="memory://metadata-audit",
            metadata={
                "simulated_text": "shock delivered",
                "speaker_label": "speaker_2",
                "speaker_role": "recorder",
                "acoustic_observation_type": AcousticObservationType.DEFIBRILLATOR_DISCHARGE.value,
            },
        )
    )

    records = store.records("metadata-audit")
    audio_records = [
        record for record in records if record["record_type"] == "audio_chunk_ingested"
    ]
    assert audio_records
    payload = audio_records[0]["payload"]
    assert payload["diarization"]["turns"][0]["speaker_id"] == "speaker_2"
    assert payload["diarization"]["turns"][0]["role_hypothesis"]["role"] == "recorder"
    assert payload["acoustic_events"]["detections"][0]["acoustic_type"] == "defibrillator_discharge"


def test_correction_requires_explicit_payload() -> None:
    session = DemoWorkflowSession()
    result = session.process_transcript(
        DemoTranscriptRequest(text="give epinephrine", confidence=1.0)
    )
    request = result.confirmation_requests[0]

    with pytest.raises(ValueError, match="event_type is required"):
        session.correct_voice_candidate(
            DemoCorrectionActionRequest(confirmation_request_id=request.id)
        )

    with pytest.raises(ValueError, match="completed_action"):
        session.correct_voice_candidate(
            DemoCorrectionActionRequest(
                confirmation_request_id=request.id,
                event_type=EventType.RHYTHM_CHECKED,
                payload={"rhythm": "pea"},
            )
        )


def test_rhythm_correction_updates_engine() -> None:
    session = DemoWorkflowSession()
    result = session.process_transcript(
        DemoTranscriptRequest(text="give epinephrine", confidence=1.0)
    )
    request = result.confirmation_requests[0]

    corrected = session.correct_voice_candidate(
        DemoCorrectionActionRequest(
            confirmation_request_id=request.id,
            event_type=EventType.RHYTHM_CHECKED,
            payload={"rhythm": "pea"},
            observation_kind="completed_action",
            resolved_by="demo-clinician",
        )
    )

    event = corrected.fusion_results[0].candidate_event
    assert event is not None
    assert event.status == EventStatus.CORRECTED
    assert event.supersedes_event_id is not None
    assert event.payload["rhythm"] == "pea"
    assert corrected.state.rhythm_state.current_rhythm == RhythmName.PEA
    assert corrected.accepted_event_ids == [str(event.id)]


def test_medication_correction_preserves_dose_route_and_updates_timeline() -> None:
    session = DemoWorkflowSession()
    result = session.process_transcript(
        DemoTranscriptRequest(text="give epinephrine", confidence=1.0)
    )
    request = result.confirmation_requests[0]

    corrected = session.correct_voice_candidate(
        DemoCorrectionActionRequest(
            confirmation_request_id=request.id,
            event_type=EventType.MEDICATION_GIVEN,
            payload={
                "medication": "epinephrine",
                "dose": 1,
                "unit": "mg",
                "route": "IO",
            },
            observation_kind="completed_action",
        )
    )

    event = corrected.fusion_results[0].candidate_event
    assert event is not None
    assert event.status == EventStatus.CORRECTED
    assert event.payload == {
        "medication": "epinephrine",
        "dose": 1,
        "unit": "mg",
        "route": "IO",
    }
    assert corrected.state.medication_state.epinephrine_count == 1
    assert corrected.state.timeline[-1].payload["route"] == "IO"


def test_conflict_candidate_cannot_be_confirmed_but_can_be_corrected() -> None:
    session = DemoWorkflowSession()
    result = DeterministicEvidenceFusionEngine().fuse(
        [
            Evidence(
                source=EventSource.SPEECH,
                evidence_type="normalized_clinical_observation",
                confidence=1.0,
                payload={
                    "event_type": EventType.RHYTHM_CHECKED.value,
                    "payload": {"rhythm": "vf"},
                    "observation_kind": "observation",
                    "is_positive": True,
                },
            ),
            Evidence(
                source=EventSource.SPEECH,
                evidence_type="normalized_clinical_observation",
                confidence=1.0,
                payload={
                    "event_type": EventType.RHYTHM_CHECKED.value,
                    "payload": {"rhythm": "pea"},
                    "observation_kind": "observation",
                    "is_positive": True,
                },
            ),
        ]
    )
    assert result.candidate_event is not None
    candidate_id = str(result.candidate_event.id)
    session._last_voice_fusion_results = [result]
    session._pending_fusion_results[candidate_id] = result
    confirmation = ConfirmationRequest(
        id="conflict-confirmation",
        candidate_event_id=candidate_id,
        reason="conflicting_evidence",
        confidence=result.candidate_event.confidence,
        options=[
            ConfirmationOption.CONFIRM,
            ConfirmationOption.REJECT,
            ConfirmationOption.CORRECT,
        ],
    )
    session._pending_confirmation_requests[confirmation.id] = confirmation

    with pytest.raises(ValueError, match="requires rejection or explicit correction"):
        session.confirm_voice_candidate(
            DemoConfirmationActionRequest(confirmation_request_id=confirmation.id)
        )

    corrected = session.correct_voice_candidate(
        DemoCorrectionActionRequest(
            confirmation_request_id=confirmation.id,
            event_type=EventType.RHYTHM_CHECKED,
            payload={"rhythm": "vf"},
            observation_kind="completed_action",
        )
    )

    assert corrected.state.rhythm_state.current_rhythm == RhythmName.VF
    assert corrected.fusion_results[0].uncertainty_reason == "human_corrected"


def test_negative_evidence_only_cannot_be_corrected_without_candidate_target() -> None:
    session = DemoWorkflowSession()
    result = session.process_transcript(DemoTranscriptRequest(text="no shock", confidence=1.0))

    assert result.fusion_results[0].candidate_event is None
    with pytest.raises(ValueError, match="unknown or already resolved"):
        session.correct_voice_candidate(
            DemoCorrectionActionRequest(
                candidate_event_id="not-a-candidate",
                event_type=EventType.SHOCK_DELIVERED,
                payload={},
                observation_kind="completed_action",
            )
        )


def test_persisted_session_can_replay_accepted_and_corrected_timeline(tmp_path) -> None:
    store = DemoAuditStore(tmp_path / "pulse-audit.jsonl")
    session = DemoWorkflowSession(audit_store=store, session_id="audit-session")
    result = session.process_transcript(
        DemoTranscriptRequest(text="give epinephrine", confidence=1.0)
    )
    session.correct_voice_candidate(
        DemoCorrectionActionRequest(
            confirmation_request_id=result.confirmation_requests[0].id,
            event_type=EventType.RHYTHM_CHECKED,
            payload={"rhythm": "pea"},
            observation_kind="completed_action",
        )
    )

    replayed = session.replay_persisted_timeline("audit-session")

    assert replayed.rhythm_state.current_rhythm == RhythmName.PEA
    assert len(replayed.timeline) == 1
    assert replayed.timeline[0].event_type == EventType.RHYTHM_CHECKED.value


def test_rejected_and_negative_evidence_persist_but_do_not_enter_engine(tmp_path) -> None:
    store = DemoAuditStore(tmp_path / "pulse-audit.jsonl")
    session = DemoWorkflowSession(audit_store=store, session_id="audit-session")
    negative = session.process_transcript(DemoTranscriptRequest(text="no pulse", confidence=1.0))
    result = session.process_transcript(
        DemoTranscriptRequest(text="give epinephrine", confidence=1.0)
    )
    session.reject_voice_candidate(
        DemoConfirmationActionRequest(
            confirmation_request_id=result.confirmation_requests[0].id
        )
    )

    records = store.records("audit-session")
    replayed = session.replay_persisted_timeline("audit-session")

    assert negative.fusion_results[0].result_kind == "negative_evidence"
    assert any(record["record_type"] == "voice_fusion_result" for record in records)
    assert any(
        record["record_type"] == "confirmation_action"
        and record["payload"]["action"] == "reject"
        for record in records
    )
    assert store.accepted_or_corrected_events("audit-session") == []
    assert replayed.timeline == []


def test_compound_utterance_applies_resume_after_shock() -> None:
    session = DemoWorkflowSession()
    start = session.start_live_voice_session(LiveVoiceSessionStartRequest())
    session_id = start.session.session_id
    base = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)

    for sequence, (offset, text) in enumerate(
        [(0, "cpr started"), (5, "rhythm is vf"), (10, "shock delivered resume cpr")],
        start=1,
    ):
        session.ingest_live_transcript_chunk(
            LiveTranscriptChunkRequest(
                session_id=session_id,
                sequence=sequence,
                text=text,
                confidence=0.95,
                timestamp=base + timedelta(seconds=offset),
            )
        )

    state = session.current_state()
    assert state.shock_count == 1
    assert state.cpr_status == "Active"


def test_compound_utterance_reversed_text_order_still_ends_active() -> None:
    session = DemoWorkflowSession()
    start = session.start_live_voice_session(LiveVoiceSessionStartRequest())
    session_id = start.session.session_id
    base = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)

    for sequence, (offset, text) in enumerate(
        [(0, "cpr started"), (5, "rhythm is vf"), (10, "resume cpr shock delivered")],
        start=1,
    ):
        session.ingest_live_transcript_chunk(
            LiveTranscriptChunkRequest(
                session_id=session_id,
                sequence=sequence,
                text=text,
                confidence=0.95,
                timestamp=base + timedelta(seconds=offset),
            )
        )

    state = session.current_state()
    assert state.shock_count == 1
    assert state.cpr_status == "Active"
