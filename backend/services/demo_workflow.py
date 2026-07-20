from datetime import UTC, datetime, timedelta
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from backend.ai.copilot import CopilotRequest, CopilotResponse, StateBoundClinicalCopilot
from backend.services.confirmation import ConfirmationRequest
from backend.services.evidence_fusion import FusionResult
from backend.services.multimodal_voice_pipeline import (
    DeterministicMultimodalVoicePipeline,
    MultimodalTranscriptIngestRequest,
)
from backend.audio.asr import (
    AudioTranscriptionRequest,
    TranscriptChunkResult,
    TranscriptionProvider,
    TranscriptionProviderConfigurationError,
    TranscriptionProviderRuntimeError,
    configured_transcription_provider,
    provider_status,
)
from backend.audio.acoustic import (
    AcousticEventProvider,
    AcousticEventProviderConfigurationError,
    AcousticEventRequest,
    AcousticEventResult,
    configured_acoustic_event_provider,
    detection_to_acoustic_observation,
)
from backend.audio.diarization import (
    DiarizationProvider,
    DiarizationProviderConfigurationError,
    DiarizationRequest,
    DiarizationResult,
    configured_diarization_provider,
)
from backend.audio.multimodal import AudioChunk, SpeakerRoleHypothesis, TranscriptLanguage
from backend.audio.storage import (
    LiveAudioChunkStore,
    LiveAudioStorageError,
)
from backend.services.audit_store import DemoAuditStore
from backend.workflow.coordinator import (
    ActionKind,
    OwnedRecommendation,
    WorkflowCoordinator,
    WorkflowCoordinatorInput,
    WorkflowPhase,
    WorkflowPresentationDecision,
)
from backend.workflow.cpr import CPRState, CPRStateMachine
from backend.workflow.engine import ClinicalWorkflowEngine
from backend.workflow.event_processor import MachineRegistry, RoutingTable
from backend.workflow.events import (
    ClinicalEvent,
    CorrectionRecord,
    EventSource,
    EventStatus,
    EventType,
    Evidence,
)
from backend.workflow.medications import MedicationState, MedicationStateMachine
from backend.workflow.recommendations import Recommendation
from backend.workflow.hs_ts import ReversibleCauseState, ReversibleCauseStateMachine
from backend.workflow.rhythm import RhythmName, RhythmState, RhythmStateMachine
from backend.workflow.rosc import ROSCState, ROSCStateMachine
from backend.workflow.shocks import ShockState, ShockStateMachine

DemoAction = Literal[
    "cpr_started",
    "cpr_paused",
    "cpr_resumed",
    "vf",
    "pvt",
    "asystole",
    "pea",
    "shock_delivered",
    "epinephrine_given",
    "amiodarone_given",
    "rosc",
]

# When one utterance yields several events with the same timestamp, apply them
# to the engine in clinical precedence order. Resuming compressions is always
# the closing statement of a compound utterance ("shock delivered resume cpr"),
# so cpr_resumed applies last; otherwise the shock's pause side effect would
# overwrite the resume depending on extraction order.
_ENGINE_APPLICATION_PRECEDENCE: dict[EventType, int] = {
    EventType.CPR_STARTED: 0,
    EventType.RHYTHM_CHECKED: 1,
    EventType.MEDICATION_GIVEN: 2,
    EventType.SHOCK_DELIVERED: 3,
    EventType.CPR_PAUSED: 4,
    EventType.ROSC_ACHIEVED: 5,
    EventType.CPR_RESUMED: 6,
}
_DEFAULT_APPLICATION_PRECEDENCE = 2


class DemoEventRequest(BaseModel):
    action: DemoAction


class DemoTranscriptRequest(BaseModel):
    text: str
    confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    speaker_label: str | None = None
    timestamp: datetime | None = None


class DemoUndoRequest(BaseModel):
    event_id: str


class DemoConfirmationActionRequest(BaseModel):
    candidate_event_id: str | None = None
    confirmation_request_id: str | None = None
    resolved_by: str | None = None


class DemoCorrectionActionRequest(BaseModel):
    candidate_event_id: str | None = None
    confirmation_request_id: str | None = None
    resolved_by: str | None = None
    event_type: EventType | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    observation_kind: Literal["completed_action"] | None = None


class LiveVoiceSessionStartRequest(BaseModel):
    script_name: str = "v0.4-demo"


class LiveVoiceSessionActionRequest(BaseModel):
    session_id: str


class LiveTranscriptChunkRequest(BaseModel):
    session_id: str
    sequence: int = Field(ge=1)
    text: str
    confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    timestamp: datetime | None = None
    speaker_label: str | None = None
    language: TranscriptLanguage = TranscriptLanguage.UNKNOWN


class LiveAudioSessionStartRequest(BaseModel):
    provider_name: str | None = None


class LiveAudioSessionActionRequest(BaseModel):
    session_id: str


class LiveAudioChunkRequest(BaseModel):
    session_id: str
    sequence: int = Field(ge=1)
    audio_reference: str
    content_type: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    timestamp: datetime | None = None
    sample_rate_hz: int | None = Field(default=None, gt=0)
    channel_count: int | None = Field(default=None, gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LiveAudioUploadRequest(BaseModel):
    session_id: str
    sequence: int = Field(ge=1)
    content_type: str
    content: bytes
    timestamp: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class LiveVoiceSessionSummary(BaseModel):
    session_id: str
    active: bool
    script_name: str
    started_at: datetime
    stopped_at: datetime | None = None
    next_sequence: int
    chunk_count: int


class LiveTranscriptChunk(BaseModel):
    session_id: str
    sequence: int
    text: str
    confidence: float
    timestamp: datetime
    speaker_label: str | None = None
    language: TranscriptLanguage


class LiveVoiceSessionResponse(BaseModel):
    session: LiveVoiceSessionSummary
    chunks: list[LiveTranscriptChunk] = Field(default_factory=list)
    state: "DemoStateResponse"


class LiveTranscriptChunkResponse(BaseModel):
    session: LiveVoiceSessionSummary
    chunk: LiveTranscriptChunk
    result: "DemoTranscriptResponse"


class LiveAudioSessionSummary(BaseModel):
    session_id: str
    active: bool
    provider_name: str
    provider_mode: str
    provider_available: bool
    fallback_provider_name: str | None = None
    provider_error: str | None = None
    started_at: datetime
    stopped_at: datetime | None = None
    next_sequence: int
    chunk_count: int


class LiveAudioChunk(BaseModel):
    session_id: str
    sequence: int
    audio_reference: str
    content_type: str | None = None
    duration_ms: int | None = None
    timestamp: datetime
    sample_rate_hz: int | None = None
    channel_count: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class LiveAudioSessionResponse(BaseModel):
    session: LiveAudioSessionSummary
    chunks: list[LiveAudioChunk] = Field(default_factory=list)
    state: "DemoStateResponse"


class LiveAudioChunkResponse(BaseModel):
    session: LiveAudioSessionSummary
    audio_chunk: LiveAudioChunk
    transcript: TranscriptChunkResult | None = None
    diarization: DiarizationResult
    acoustic_events: AcousticEventResult
    result: "DemoTranscriptResponse | None" = None
    transcription_error: str | None = None


class LiveScriptedStreamResponse(BaseModel):
    session: LiveVoiceSessionSummary
    chunk: LiveTranscriptChunk | None = None
    result: "DemoTranscriptResponse | None" = None
    is_complete: bool = False


class DemoTimelineEntry(BaseModel):
    id: str
    timestamp: datetime
    event_type: str
    label: str
    payload: dict[str, str] = Field(default_factory=dict)


class DemoStateResponse(BaseModel):
    current_workflow_phase: WorkflowPhase
    current_rhythm: str
    current_pathway: str
    cpr_status: str
    cpr_cycle_number: int
    cpr_cycle_elapsed_seconds: int | None = None
    cpr_hands_off_elapsed_seconds: int | None = None
    shock_count: int
    medication_history: list[str]
    rosc_status: str
    primary_action: str
    secondary_actions: list[str]
    clinical_rationale: str
    safety_flags: list[str] = Field(default_factory=list)
    undoable_event_ids: list[str] = Field(default_factory=list)
    rhythm_state: RhythmState
    cpr_state: CPRState
    shock_state: ShockState
    medication_state: MedicationState
    rosc_state: ROSCState
    reversible_cause_state: ReversibleCauseState
    top_reversible_causes: list[str]
    recommendations: list[OwnedRecommendation]
    coordinator_decision: WorkflowPresentationDecision
    timeline: list[DemoTimelineEntry]

    @property
    def next_recommended_action(self) -> str:
        return self.primary_action


class DemoTranscriptResponse(BaseModel):
    state: DemoStateResponse
    fusion_results: list[FusionResult]
    confirmation_requests: list[ConfirmationRequest]
    accepted_event_ids: list[str]
    undoable_event_ids: list[str] = Field(default_factory=list)
    evidence: list["DemoEvidenceSummary"] = Field(default_factory=list)


class DemoEvidenceSummary(BaseModel):
    id: str
    source: str
    evidence_type: str
    confidence: float
    raw_reference: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class DemoAcceptedEventSummary(BaseModel):
    id: str
    event_type: str
    confidence: float
    status: str
    payload: dict[str, str] = Field(default_factory=dict)


class DemoEngineStateSnapshot(BaseModel):
    rhythm: str
    pathway: str
    shock_count: int
    medication_history: list[str]
    rosc_status: str


class DemoScenarioTimelineEntry(BaseModel):
    transcript: str
    evidence: list[DemoEvidenceSummary]
    evidence_ids: list[str] = Field(default_factory=list)
    result_kind: str | None = None
    confidence: float | None = None
    fusion_decision: str
    accepted_event: DemoAcceptedEventSummary | None = None
    engine_state: DemoEngineStateSnapshot
    recommendation: str
    secondary_recommendations: list[str]
    rationale: str


class DemoScenarioResponse(BaseModel):
    title: str
    state: DemoStateResponse
    timeline: list[DemoScenarioTimelineEntry]


@dataclass
class _LiveVoiceSession:
    session_id: str
    script_name: str
    started_at: datetime
    active: bool = True
    stopped_at: datetime | None = None
    next_sequence: int = 1
    chunks: list[LiveTranscriptChunk] = field(default_factory=list)
    scripted_index: int = 0


@dataclass
class _LiveAudioSession:
    session_id: str
    provider_name: str
    started_at: datetime
    active: bool = True
    stopped_at: datetime | None = None
    next_sequence: int = 1
    chunks: list[LiveAudioChunk] = field(default_factory=list)


_SCRIPTED_LIVE_CHUNKS: tuple[tuple[str, float, str | None, TranscriptLanguage], ...] = (
    ("ادي ادرينالين", 0.95, "nurse", TranscriptLanguage.EGYPTIAN_ARABIC),
    ("epi is in 1 mg IV", 0.98, "nurse", TranscriptLanguage.ENGLISH),
    ("مفيش نبض", 0.96, "physician", TranscriptLanguage.EGYPTIAN_ARABIC),
    ("Rhythm is VF.", 0.97, "team_leader", TranscriptLanguage.ENGLISH),
    ("shock اتعمل", 0.96, "recorder", TranscriptLanguage.MIXED),
)


class DemoWorkflowSession:
    """In-memory demo session using the real deterministic workflow classes."""

    def __init__(
        self,
        *,
        transcription_provider: TranscriptionProvider | None = None,
        diarization_provider: DiarizationProvider | None = None,
        acoustic_event_provider: AcousticEventProvider | None = None,
        audio_chunk_store: LiveAudioChunkStore | None = None,
        audit_store: DemoAuditStore | None = None,
        session_id: str | None = None,
        audit_enabled: bool = True,
    ) -> None:
        self.session_id = session_id or f"demo-session-{uuid4()}"
        self._audit_store = audit_store or DemoAuditStore()
        self._audit_enabled = audit_enabled
        self._registry = MachineRegistry()
        self._routing = RoutingTable(
            {
                EventType.CPR_STARTED: ["cpr"],
                EventType.CPR_PAUSED: ["cpr"],
                EventType.CPR_RESUMED: ["cpr"],
                EventType.RHYTHM_CHECKED: ["rhythm", "shocks", "medications", "cpr"],
                EventType.SHOCK_DELIVERED: ["shocks", "cpr", "medications"],
                EventType.MEDICATION_GIVEN: ["medications"],
                EventType.ROSC_ACHIEVED: ["rhythm", "shocks", "medications", "cpr", "rosc"],
                EventType.REVERSIBLE_CAUSE_CONSIDERED: ["reversible_causes"],
            }
        )
        self._engine = ClinicalWorkflowEngine(
            registry=self._registry,
            routing_table=self._routing,
        )
        self._rhythm_machine = RhythmStateMachine()
        self._cpr_machine = CPRStateMachine()
        self._shock_machine = ShockStateMachine()
        self._medication_machine = MedicationStateMachine()
        self._rosc_machine = ROSCStateMachine()
        self._reversible_cause_machine = ReversibleCauseStateMachine()
        self._coordinator = WorkflowCoordinator()
        self._voice_pipeline = DeterministicMultimodalVoicePipeline()
        self._transcription_provider = (
            transcription_provider or configured_transcription_provider()
        )
        self._diarization_provider = (
            diarization_provider or configured_diarization_provider()
        )
        self._acoustic_event_provider = (
            acoustic_event_provider or configured_acoustic_event_provider()
        )
        self._audio_chunk_store = audio_chunk_store or LiveAudioChunkStore()
        self._copilot = StateBoundClinicalCopilot()
        self._engine.register_machine("rhythm", self._rhythm_machine)
        self._engine.register_machine("cpr", self._cpr_machine)
        self._engine.register_machine("shocks", self._shock_machine)
        self._engine.register_machine("medications", self._medication_machine)
        self._engine.register_machine("rosc", self._rosc_machine)
        self._engine.register_machine("reversible_causes", self._reversible_cause_machine)
        self._timeline: list[ClinicalEvent] = []
        self._last_voice_fusion_results: list[FusionResult] = []
        self._last_voice_evidence: list[DemoEvidenceSummary] = []
        self._pending_fusion_results: dict[str, FusionResult] = {}
        self._pending_confirmation_requests: dict[str, ConfirmationRequest] = {}
        self._safety_flags: list[str] = []
        self._undoable_event_ids: dict[str, datetime] = {}
        self._live_session_counter = 0
        self._live_sessions: dict[str, _LiveVoiceSession] = {}
        self._live_audio_session_counter = 0
        self._live_audio_sessions: dict[str, _LiveAudioSession] = {}
        self._audit(
            "session_started",
            {
                "session_id": self.session_id,
                "transcription_provider": self._transcription_provider.provider_name,
                "diarization_provider": self._diarization_provider.provider_name,
                "acoustic_event_provider": self._acoustic_event_provider.provider_name,
            },
        )

    def process_action(self, action: DemoAction) -> DemoStateResponse:
        event = self._event_for_action(action)
        self._process_engine_event(event)
        return self.current_state()

    def process_transcript(self, request: DemoTranscriptRequest) -> DemoTranscriptResponse:
        pipeline_result = self._voice_pipeline.ingest_transcript(
            MultimodalTranscriptIngestRequest(
                text=request.text,
                confidence=request.confidence,
                speaker_id=request.speaker_label,
                timestamp=request.timestamp or datetime.now(UTC),
            )
        )
        self._audit(
            "transcript_ingested",
            {
                "text": request.text,
                "confidence": request.confidence,
                "speaker_label": request.speaker_label,
                "timestamp": (request.timestamp or datetime.now(UTC)).isoformat(),
            },
        )
        return self._voice_response_from_pipeline_result(
            pipeline_result,
            replace_voice_review=True,
        )

    def start_live_voice_session(
        self,
        request: LiveVoiceSessionStartRequest,
    ) -> LiveVoiceSessionResponse:
        self._live_session_counter += 1
        session = _LiveVoiceSession(
            session_id=f"demo-live-{self._live_session_counter}",
            script_name=request.script_name,
            started_at=datetime.now(UTC),
        )
        self._live_sessions[session.session_id] = session
        self._audit(
            "live_voice_session_started",
            {"live_session_id": session.session_id, "script_name": session.script_name},
        )
        return LiveVoiceSessionResponse(
            session=self._live_session_summary(session),
            chunks=list(session.chunks),
            state=self.current_state(),
        )

    def stop_live_voice_session(
        self,
        request: LiveVoiceSessionActionRequest,
    ) -> LiveVoiceSessionResponse:
        session = self._live_session_for(request.session_id)
        session.active = False
        session.stopped_at = datetime.now(UTC)
        self._audit(
            "live_voice_session_stopped",
            {"live_session_id": session.session_id, "stopped_at": session.stopped_at.isoformat()},
        )
        return LiveVoiceSessionResponse(
            session=self._live_session_summary(session),
            chunks=list(session.chunks),
            state=self.current_state(),
        )

    def ingest_live_transcript_chunk(
        self,
        request: LiveTranscriptChunkRequest,
    ) -> LiveTranscriptChunkResponse:
        session = self._live_session_for(request.session_id)
        if not session.active:
            raise ValueError("live voice session is stopped")
        if request.sequence != session.next_sequence:
            raise ValueError("transcript chunk sequence is out of order")

        timestamp = request.timestamp or datetime.now(UTC)
        chunk = LiveTranscriptChunk(
            session_id=session.session_id,
            sequence=request.sequence,
            text=request.text,
            confidence=request.confidence,
            timestamp=timestamp,
            speaker_label=request.speaker_label,
            language=request.language,
        )
        pipeline_result = self._voice_pipeline.ingest_transcript(
            MultimodalTranscriptIngestRequest(
                text=request.text,
                confidence=request.confidence,
                language=request.language,
                speaker_id=request.speaker_label,
                timestamp=timestamp,
                session_id=session.session_id,
            )
        )
        session.chunks.append(chunk)
        session.next_sequence += 1
        self._audit(
            "transcript_chunk_ingested",
            {"chunk": chunk.model_dump(mode="json")},
        )
        response = self._voice_response_from_pipeline_result(
            pipeline_result,
            replace_voice_review=False,
        )
        return LiveTranscriptChunkResponse(
            session=self._live_session_summary(session),
            chunk=chunk,
            result=response,
        )

    def advance_scripted_live_stream(
        self,
        request: LiveVoiceSessionActionRequest,
    ) -> LiveScriptedStreamResponse:
        session = self._live_session_for(request.session_id)
        if session.scripted_index >= len(_SCRIPTED_LIVE_CHUNKS):
            return LiveScriptedStreamResponse(
                session=self._live_session_summary(session),
                is_complete=True,
            )

        text, confidence, speaker_label, language = _SCRIPTED_LIVE_CHUNKS[
            session.scripted_index
        ]
        session.scripted_index += 1
        response = self.ingest_live_transcript_chunk(
            LiveTranscriptChunkRequest(
                session_id=session.session_id,
                sequence=session.next_sequence,
                text=text,
                confidence=confidence,
                timestamp=datetime.now(UTC),
                speaker_label=speaker_label,
                language=language,
            )
        )
        return LiveScriptedStreamResponse(
            session=response.session,
            chunk=response.chunk,
            result=response.result,
            is_complete=False,
        )

    def start_live_audio_session(
        self,
        request: LiveAudioSessionStartRequest,
    ) -> LiveAudioSessionResponse:
        if request.provider_name and request.provider_name != self._transcription_provider.provider_name:
            raise ValueError(
                f"configured ASR provider is {self._transcription_provider.provider_name}"
            )
        self._live_audio_session_counter += 1
        session = _LiveAudioSession(
            session_id=f"demo-audio-{self._live_audio_session_counter}",
            provider_name=self._transcription_provider.provider_name,
            started_at=datetime.now(UTC),
        )
        self._live_audio_sessions[session.session_id] = session
        self._audit(
            "live_audio_session_started",
            {"audio_session_id": session.session_id, "provider_name": session.provider_name},
        )
        return LiveAudioSessionResponse(
            session=self._live_audio_session_summary(session),
            chunks=list(session.chunks),
            state=self.current_state(),
        )

    def stop_live_audio_session(
        self,
        request: LiveAudioSessionActionRequest,
    ) -> LiveAudioSessionResponse:
        session = self._live_audio_session_for(request.session_id)
        session.active = False
        session.stopped_at = datetime.now(UTC)
        self._audit(
            "live_audio_session_stopped",
            {"audio_session_id": session.session_id, "stopped_at": session.stopped_at.isoformat()},
        )
        return LiveAudioSessionResponse(
            session=self._live_audio_session_summary(session),
            chunks=list(session.chunks),
            state=self.current_state(),
        )

    def ingest_live_audio_chunk(
        self,
        request: LiveAudioChunkRequest,
    ) -> LiveAudioChunkResponse:
        session = self._live_audio_session_for(request.session_id)
        if not session.active:
            raise ValueError("live audio session is stopped")
        if request.sequence != session.next_sequence:
            raise ValueError("audio chunk sequence is out of order")

        timestamp = request.timestamp or datetime.now(UTC)
        audio_chunk = LiveAudioChunk(
            session_id=session.session_id,
            sequence=request.sequence,
            audio_reference=request.audio_reference,
            content_type=request.content_type,
            duration_ms=request.duration_ms,
            timestamp=timestamp,
            sample_rate_hz=request.sample_rate_hz,
            channel_count=request.channel_count,
            metadata=dict(request.metadata),
        )
        try:
            transcript = self._transcription_provider.transcribe(
                AudioTranscriptionRequest(
                    session_id=session.session_id,
                    sequence=request.sequence,
                    audio_reference=request.audio_reference,
                    content_type=request.content_type,
                    duration_ms=request.duration_ms,
                    timestamp=timestamp,
                    sample_rate_hz=request.sample_rate_hz,
                    channel_count=request.channel_count,
                    metadata=request.metadata,
                )
            )
        except TranscriptionProviderConfigurationError as exc:
            return self._live_audio_error_response(session, audio_chunk, str(exc))
        except TranscriptionProviderRuntimeError as exc:
            return self._live_audio_error_response(session, audio_chunk, str(exc))

        try:
            diarization = self._diarization_provider.diarize(
                DiarizationRequest(
                    session_id=session.session_id,
                    sequence=request.sequence,
                    audio_reference=request.audio_reference,
                    started_at=timestamp,
                    ended_at=transcript.ended_at,
                    duration_ms=request.duration_ms,
                    metadata=request.metadata,
                )
            )
            acoustic_events = self._acoustic_event_provider.detect(
                AcousticEventRequest(
                    session_id=session.session_id,
                    sequence=request.sequence,
                    audio_reference=request.audio_reference,
                    started_at=timestamp,
                    ended_at=transcript.ended_at,
                    duration_ms=request.duration_ms,
                    metadata=request.metadata,
                )
            )
        except DiarizationProviderConfigurationError as exc:
            raise ValueError(str(exc)) from exc
        except AcousticEventProviderConfigurationError as exc:
            raise ValueError(str(exc)) from exc

        selected_turn = diarization.turns[0] if diarization.turns else None
        speaker_id = transcript.speaker_label or (
            selected_turn.speaker_id if selected_turn is not None else None
        )
        role_hypothesis = _role_hypothesis_for_speaker(
            speaker_id,
            selected_turn.role_hypothesis if selected_turn is not None else None,
        )
        acoustic_chunk = AudioChunk(
            session_id=session.session_id,
            started_at=timestamp,
            ended_at=transcript.ended_at,
            audio_reference=request.audio_reference,
            sample_rate_hz=request.sample_rate_hz,
            channel_count=request.channel_count,
            metadata=request.metadata,
        )
        acoustic_observations = tuple(
            detection_to_acoustic_observation(
                chunk_id=acoustic_chunk.id,
                detection=detection,
            )
            for detection in acoustic_events.detections
        )

        pipeline_result = self._voice_pipeline.ingest_transcript(
            MultimodalTranscriptIngestRequest(
                text=transcript.text,
                confidence=transcript.confidence,
                language=transcript.language,
                speaker_id=speaker_id,
                diarization_confidence=(
                    selected_turn.confidence if selected_turn is not None else None
                ),
                is_overlapping=(
                    selected_turn.is_overlapping if selected_turn is not None else False
                ),
                role_hypothesis=role_hypothesis,
                timestamp=transcript.started_at,
                session_id=session.session_id,
                acoustic_observations=acoustic_observations,
            )
        )
        transcript_response = self._voice_response_from_pipeline_result(
            pipeline_result,
            replace_voice_review=False,
        )
        session.chunks.append(audio_chunk)
        session.next_sequence += 1
        self._audit(
            "audio_chunk_ingested",
            {
                "chunk": audio_chunk.model_dump(mode="json"),
                "transcript": transcript.model_dump(mode="json"),
                "diarization": diarization.model_dump(mode="json"),
                "acoustic_events": acoustic_events.model_dump(mode="json"),
                "provider_status": provider_status(self._transcription_provider).model_dump(mode="json"),
            },
        )
        return LiveAudioChunkResponse(
            session=self._live_audio_session_summary(session),
            audio_chunk=audio_chunk,
            transcript=transcript,
            diarization=diarization,
            acoustic_events=acoustic_events,
            result=transcript_response,
        )

    def confirm_voice_candidate(
        self,
        request: DemoConfirmationActionRequest,
    ) -> DemoTranscriptResponse:
        fusion_result = self._pending_result_for(request)
        event = fusion_result.candidate_event
        if event is None:
            raise ValueError("negative or evidence-only fusion results cannot be confirmed")
        if not _is_confirmable_voice_candidate(fusion_result):
            raise ValueError("candidate requires rejection or explicit correction")

        accepted_event = event.model_copy(update={"status": EventStatus.ACCEPTED})
        self._process_engine_event(accepted_event)
        updated = fusion_result.model_copy(
            update={
                "candidate_event": accepted_event,
                "requires_confirmation": False,
                "uncertainty_reason": "human_confirmed",
            }
        )
        self._replace_fusion_result(updated)
        self._clear_pending_for(event.id)
        self._audit(
            "confirmation_action",
            {
                "action": "confirm",
                "candidate_event_id": str(event.id),
                "confirmation_request_id": request.confirmation_request_id,
                "resolved_by": request.resolved_by,
            },
        )
        return DemoTranscriptResponse(
            state=self.current_state(),
            fusion_results=self._last_voice_fusion_results,
            confirmation_requests=list(self._pending_confirmation_requests.values()),
            accepted_event_ids=[str(accepted_event.id)],
            evidence=self._last_voice_evidence,
        )

    def reject_voice_candidate(
        self,
        request: DemoConfirmationActionRequest,
    ) -> DemoTranscriptResponse:
        fusion_result = self._pending_result_for(request)
        event = fusion_result.candidate_event
        if event is None:
            raise ValueError("negative or evidence-only fusion results cannot be rejected as candidates")

        rejected_event = event.model_copy(update={"status": EventStatus.REJECTED})
        updated = fusion_result.model_copy(
            update={
                "candidate_event": rejected_event,
                "requires_confirmation": False,
                "uncertainty_reason": "human_rejected",
            }
        )
        self._replace_fusion_result(updated)
        self._clear_pending_for(event.id)
        self._audit(
            "confirmation_action",
            {
                "action": "reject",
                "candidate_event_id": str(event.id),
                "confirmation_request_id": request.confirmation_request_id,
                "resolved_by": request.resolved_by,
                "event": rejected_event.model_dump(mode="json"),
            },
        )
        return DemoTranscriptResponse(
            state=self.current_state(),
            fusion_results=self._last_voice_fusion_results,
            confirmation_requests=list(self._pending_confirmation_requests.values()),
            accepted_event_ids=[],
            evidence=self._last_voice_evidence,
        )

    def correct_voice_candidate(
        self,
        request: DemoCorrectionActionRequest,
    ) -> DemoTranscriptResponse:
        fusion_result = self._pending_result_for(request)
        original_event = fusion_result.candidate_event
        if original_event is None:
            raise ValueError("negative or evidence-only fusion results require an explicit candidate target")
        event_type, payload = _validated_correction_payload(request)

        corrected_event = ClinicalEvent(
            event_type=event_type,
            source=EventSource.MANUAL,
            confidence=1.0,
            status=EventStatus.CORRECTED,
            evidence=original_event.evidence,
            supersedes_event_id=original_event.id,
            correction_history=(
                CorrectionRecord(
                    corrected_by=request.resolved_by,
                    reason="explicit_human_correction",
                    previous_status=original_event.status,
                    superseded_event_id=original_event.id,
                ),
            ),
            payload=payload,
        )
        self._process_engine_event(corrected_event)
        updated = fusion_result.model_copy(
            update={
                "candidate_event": corrected_event,
                "requires_confirmation": False,
                "uncertainty_reason": "human_corrected",
            }
        )
        self._replace_fusion_result_for(original_event.id, updated)
        self._clear_pending_for(original_event.id)
        self._audit(
            "confirmation_action",
            {
                "action": "correct",
                "candidate_event_id": str(original_event.id),
                "confirmation_request_id": request.confirmation_request_id,
                "resolved_by": request.resolved_by,
                "correction": corrected_event.model_dump(mode="json"),
            },
        )
        return DemoTranscriptResponse(
            state=self.current_state(),
            fusion_results=self._last_voice_fusion_results,
            confirmation_requests=list(self._pending_confirmation_requests.values()),
            accepted_event_ids=[str(corrected_event.id)],
            evidence=self._last_voice_evidence,
        )

    def undo_auto_accepted_event(self, request: DemoUndoRequest) -> DemoTranscriptResponse:
        expires_at = self._undoable_event_ids.get(request.event_id)
        if expires_at is None or datetime.now(UTC) > expires_at:
            raise ValueError("event is not eligible for undo")
        event_index = next(
            (index for index, event in enumerate(self._timeline) if str(event.id) == request.event_id),
            None,
        )
        if event_index is None:
            raise ValueError("unknown accepted event")

        original = self._timeline[event_index]
        rejected = original.model_copy(update={"status": EventStatus.REJECTED})
        self._timeline[event_index] = rejected
        self._undoable_event_ids.pop(request.event_id, None)
        self._replace_fusion_result_for(original.id, FusionResult(
            candidate_event=rejected,
            evidence_ids=[str(item.id) for item in rejected.evidence],
            uncertainty_reason="human_undone_auto_accept",
            policy_version="v0.4.closed_loop.v1",
            result_kind="undone",
        ))
        self._rebuild_engine_from_timeline()
        self._audit(
            "auto_accepted_event_undone",
            {
                "event_id": request.event_id,
                "event_type": original.event_type.value,
                "previous_status": original.status.value,
            },
        )
        return DemoTranscriptResponse(
            state=self.current_state(),
            fusion_results=self._last_voice_fusion_results,
            confirmation_requests=list(self._pending_confirmation_requests.values()),
            accepted_event_ids=[],
            undoable_event_ids=self._active_undoable_event_ids(),
            evidence=self._last_voice_evidence,
        )

    def run_end_to_end_voice_scenario(self) -> DemoScenarioResponse:
        scenario = DemoWorkflowSession()
        timeline: list[DemoScenarioTimelineEntry] = []
        transcripts = (
            "VF detected",
            "Shock delivered",
            "Adrenaline 1 mg given",
            "Shock delivered",
            "Shock delivered",
            "Patient still in VF after shocks",
            "ROSC achieved",
        )

        for transcript in transcripts:
            response = scenario.process_transcript(
                DemoTranscriptRequest(text=transcript, confidence=0.95)
            )
            timeline.append(
                scenario._scenario_timeline_entry(
                    transcript=transcript,
                    response=response,
                )
            )

        return DemoScenarioResponse(
            title="Pulse end-to-end voice and evidence fusion demo",
            state=scenario.current_state(),
            timeline=timeline,
        )

    def copilot_note(self) -> CopilotResponse:
        state = self.current_state()
        return self._copilot.generate(
            CopilotRequest(
                coordinator_decision=state.coordinator_decision,
                timeline=tuple(self._timeline),
                accepted_events=tuple(self._timeline),
                machine_states={
                    "rhythm": state.rhythm_state,
                    "cpr": state.cpr_state,
                    "shocks": state.shock_state,
                    "medications": state.medication_state,
                    "rosc": state.rosc_state,
                    "reversible_causes": state.reversible_cause_state,
                },
            )
        )

    def current_state(self) -> DemoStateResponse:
        return self._current_state_as_of(datetime.now(UTC))

    def reset(self) -> DemoStateResponse:
        self.__init__(
            transcription_provider=self._transcription_provider,
            diarization_provider=self._diarization_provider,
            acoustic_event_provider=self._acoustic_event_provider,
            audio_chunk_store=self._audio_chunk_store,
            audit_store=self._audit_store,
        )
        return self.current_state()

    def replay_persisted_timeline(self, session_id: str) -> DemoStateResponse:
        replay = DemoWorkflowSession(
            transcription_provider=self._transcription_provider,
            diarization_provider=self._diarization_provider,
            acoustic_event_provider=self._acoustic_event_provider,
            audio_chunk_store=self._audio_chunk_store,
            audit_store=self._audit_store,
            session_id=f"{session_id}-replay",
            audit_enabled=False,
        )
        for event in self._audit_store.accepted_or_corrected_events(session_id):
            replay._process_engine_event(event, audit=False)
        return replay.current_state()

    def _voice_response_from_pipeline_result(
        self,
        pipeline_result: Any,
        *,
        replace_voice_review: bool,
    ) -> DemoTranscriptResponse:
        fusion_results = list(pipeline_result.fusion_results)
        evidence = [
            self._evidence_summary(item)
            for item in pipeline_result.evidence
        ]
        accepted_event_ids: list[str] = []

        def _application_order(index: int) -> tuple[datetime, int, int]:
            candidate = fusion_results[index].candidate_event
            if candidate is None:
                return (datetime.min.replace(tzinfo=UTC), _DEFAULT_APPLICATION_PRECEDENCE, index)
            return (
                candidate.timestamp,
                _ENGINE_APPLICATION_PRECEDENCE.get(
                    candidate.event_type, _DEFAULT_APPLICATION_PRECEDENCE
                ),
                index,
            )

        for index in sorted(range(len(fusion_results)), key=_application_order):
            result = fusion_results[index]
            event = result.candidate_event
            if event is None or event.status != EventStatus.ACCEPTED:
                continue
            if self._is_duplicate_accepted_event(event):
                deduplicated = event.model_copy(update={"status": EventStatus.REJECTED})
                fusion_results[index] = result.model_copy(
                    update={
                        "candidate_event": deduplicated,
                        "requires_confirmation": False,
                        "uncertainty_reason": "duplicate_event_within_dedup_window",
                        "result_kind": "deduplicated",
                    }
                )
                self._audit(
                    "voice_event_deduplicated",
                    {
                        "event": deduplicated.model_dump(mode="json"),
                        "observation_kind": _event_observation_kind(event),
                    },
                )
                continue

            accepted_event = self._supersede_matching_pending_command(event)
            if accepted_event != event:
                fusion_results[index] = result.model_copy(update={"candidate_event": accepted_event})
            self._record_shock_safety_flag(accepted_event)
            self._process_engine_event(accepted_event)
            accepted_event_ids.append(str(accepted_event.id))
            if _is_closed_loop_auto_accepted(accepted_event):
                self._undoable_event_ids[str(accepted_event.id)] = datetime.now(UTC) + timedelta(seconds=30)
                self._audit(
                    "voice_event_auto_accepted",
                    {"event": accepted_event.model_dump(mode="json"), "undo_expires_in_seconds": 30},
                )

        if replace_voice_review:
            self._last_voice_fusion_results = fusion_results
            self._last_voice_evidence = evidence
        else:
            self._last_voice_fusion_results.extend(fusion_results)
            self._last_voice_evidence.extend(evidence)
        self._store_pending_confirmations(
            fusion_results,
            list(pipeline_result.confirmation_requests),
            replace=replace_voice_review,
        )

        response = DemoTranscriptResponse(
            state=self.current_state(),
            fusion_results=self._last_voice_fusion_results,
            confirmation_requests=list(self._pending_confirmation_requests.values()),
            accepted_event_ids=accepted_event_ids,
            undoable_event_ids=self._active_undoable_event_ids(),
            evidence=self._last_voice_evidence,
        )
        self._audit_voice_response(response)
        return response

    def _live_session_for(self, session_id: str) -> _LiveVoiceSession:
        session = self._live_sessions.get(session_id)
        if session is None:
            raise ValueError("unknown live voice session")
        return session

    def _live_audio_session_for(self, session_id: str) -> _LiveAudioSession:
        session = self._live_audio_sessions.get(session_id)
        if session is None:
            raise ValueError("unknown live audio session")
        return session

    @staticmethod
    def _live_session_summary(session: _LiveVoiceSession) -> LiveVoiceSessionSummary:
        return LiveVoiceSessionSummary(
            session_id=session.session_id,
            active=session.active,
            script_name=session.script_name,
            started_at=session.started_at,
            stopped_at=session.stopped_at,
            next_sequence=session.next_sequence,
            chunk_count=len(session.chunks),
        )

    def _live_audio_session_summary(self, session: _LiveAudioSession) -> LiveAudioSessionSummary:
        status = provider_status(self._transcription_provider)
        return LiveAudioSessionSummary(
            session_id=session.session_id,
            active=session.active,
            provider_name=status.provider_name,
            provider_mode=status.mode,
            provider_available=status.available,
            fallback_provider_name=status.fallback_provider_name,
            provider_error=status.error,
            started_at=session.started_at,
            stopped_at=session.stopped_at,
            next_sequence=session.next_sequence,
            chunk_count=len(session.chunks),
        )

    def _live_audio_error_response(
        self,
        session: _LiveAudioSession,
        audio_chunk: LiveAudioChunk,
        error: str,
    ) -> LiveAudioChunkResponse:
        session.chunks.append(audio_chunk)
        session.next_sequence += 1
        diarization = DiarizationResult(
            provider_name=self._diarization_provider.provider_name,
            session_id=session.session_id,
            sequence=audio_chunk.sequence,
            turns=(),
            metadata={"skipped": "asr_failed"},
        )
        acoustic_events = AcousticEventResult(
            provider_name=self._acoustic_event_provider.provider_name,
            session_id=session.session_id,
            sequence=audio_chunk.sequence,
            detections=(),
            metadata={"skipped": "asr_failed"},
        )
        self._audit(
            "audio_chunk_ingested",
            {
                "chunk": audio_chunk.model_dump(mode="json"),
                "transcript": None,
                "diarization": diarization.model_dump(mode="json"),
                "acoustic_events": acoustic_events.model_dump(mode="json"),
                "transcription_error": error,
                "provider_status": provider_status(self._transcription_provider).model_dump(mode="json"),
            },
        )
        return LiveAudioChunkResponse(
            session=self._live_audio_session_summary(session),
            audio_chunk=audio_chunk,
            transcript=None,
            diarization=diarization,
            acoustic_events=acoustic_events,
            result=None,
            transcription_error=error,
        )

    def ingest_live_audio_upload(
        self,
        request: LiveAudioUploadRequest,
    ) -> LiveAudioChunkResponse:
        session = self._live_audio_session_for(request.session_id)
        if not session.active:
            raise ValueError("live audio session is stopped")
        if request.sequence != session.next_sequence:
            raise ValueError("audio chunk sequence is out of order")
        try:
            stored = self._audio_chunk_store.store(
                session_id=session.session_id,
                sequence=request.sequence,
                content=request.content,
                content_type=request.content_type,
            )
        except LiveAudioStorageError as exc:
            raise ValueError(str(exc)) from exc

        metadata = dict(request.metadata)
        metadata.update(
            {
                "uploaded_audio": True,
                "storage_reference": stored.storage_reference,
                "file_size_bytes": stored.file_size_bytes,
                "stored_at": stored.stored_at.isoformat(),
                "expires_at": stored.expires_at.isoformat(),
            }
        )
        return self.ingest_live_audio_chunk(
            LiveAudioChunkRequest(
                session_id=session.session_id,
                sequence=request.sequence,
                audio_reference=stored.storage_reference,
                content_type=stored.content_type,
                duration_ms=None,
                timestamp=request.timestamp,
                metadata=metadata,
            )
        )

    def _store_pending_confirmations(
        self,
        fusion_results: list[FusionResult],
        confirmation_requests: list[ConfirmationRequest],
        *,
        replace: bool,
    ) -> None:
        if replace:
            self._pending_fusion_results = {}
            self._pending_confirmation_requests = {}
        for result in fusion_results:
            event = result.candidate_event
            if result.requires_confirmation and event is not None:
                self._pending_fusion_results[str(event.id)] = result
        for request in confirmation_requests:
            if request.candidate_event_id in self._pending_fusion_results:
                self._pending_confirmation_requests[request.id] = request

    def _is_duplicate_accepted_event(self, event: ClinicalEvent) -> bool:
        window = _deduplication_window(event)
        if window is None:
            return False
        observation_kind = _event_observation_kind(event)
        for prior in reversed(self._timeline):
            if prior.status not in {EventStatus.ACCEPTED, EventStatus.CORRECTED}:
                continue
            if prior.event_type != event.event_type:
                continue
            if dict(prior.payload) != dict(event.payload):
                continue
            if _event_observation_kind(prior) != observation_kind:
                continue
            elapsed = event.timestamp - prior.timestamp
            if timedelta(0) <= elapsed <= window:
                return True
        return False

    def _supersede_matching_pending_command(self, event: ClinicalEvent) -> ClinicalEvent:
        if _event_observation_kind(event) != "completed_action":
            return event
        for pending in tuple(self._pending_fusion_results.values()):
            candidate = pending.candidate_event
            if candidate is None or _event_observation_kind(candidate) not in {"command", "intent"}:
                continue
            if not _completed_action_matches_pending_command(candidate, event):
                continue
            superseded = candidate.model_copy(update={"status": EventStatus.REJECTED})
            self._replace_fusion_result_for(candidate.id, pending.model_copy(
                update={
                    "candidate_event": superseded,
                    "requires_confirmation": False,
                    "uncertainty_reason": "superseded_by_completed_action",
                    "result_kind": "superseded",
                }
            ))
            self._clear_pending_for(candidate.id)
            completed = event.model_copy(update={"supersedes_event_id": candidate.id})
            self._audit(
                "voice_command_superseded",
                {
                    "pending_command_event_id": str(candidate.id),
                    "completed_event_id": str(completed.id),
                    "event_type": event.event_type.value,
                    "payload": dict(event.payload),
                },
            )
            return completed
        return event

    def _record_shock_safety_flag(self, event: ClinicalEvent) -> None:
        if event.event_type != EventType.SHOCK_DELIVERED:
            return
        rhythm = self._rhythm_machine.get_state().current_rhythm
        if rhythm not in {RhythmName.UNKNOWN, RhythmName.PEA, RhythmName.ASYSTOLE}:
            return
        label = rhythm.value.replace("_", " ")
        flag = f"Shock recorded with last rhythm {label} — not indicated"
        if flag not in self._safety_flags:
            self._safety_flags.append(flag)
            self._audit(
                "shock_protocol_deviation",
                {"event_id": str(event.id), "last_rhythm": rhythm.value, "safety_flag": flag},
            )

    def _active_undoable_event_ids(self) -> list[str]:
        now = datetime.now(UTC)
        self._undoable_event_ids = {
            event_id: expires_at
            for event_id, expires_at in self._undoable_event_ids.items()
            if expires_at >= now
        }
        return list(self._undoable_event_ids)

    def _rebuild_engine_from_timeline(self) -> None:
        for machine in (
            self._rhythm_machine,
            self._cpr_machine,
            self._shock_machine,
            self._medication_machine,
            self._rosc_machine,
            self._reversible_cause_machine,
        ):
            machine.reset()
        for event in self._timeline:
            if event.status in {EventStatus.ACCEPTED, EventStatus.CORRECTED}:
                self._engine.process(event)

    def _pending_result_for(
        self,
        request: DemoConfirmationActionRequest,
    ) -> FusionResult:
        candidate_event_id = request.candidate_event_id
        if request.confirmation_request_id is not None:
            confirmation_request = self._pending_confirmation_requests.get(
                request.confirmation_request_id
            )
            if confirmation_request is None:
                raise ValueError("unknown confirmation request")
            candidate_event_id = confirmation_request.candidate_event_id
        if candidate_event_id is None:
            raise ValueError("candidate_event_id or confirmation_request_id is required")
        result = self._pending_fusion_results.get(candidate_event_id)
        if result is None:
            raise ValueError("unknown or already resolved candidate event")
        return result

    def _replace_fusion_result(self, updated: FusionResult) -> None:
        event = updated.candidate_event
        if event is None:
            return
        self._replace_fusion_result_for(event.id, updated)

    def _replace_fusion_result_for(
        self,
        original_event_id: object,
        updated: FusionResult,
    ) -> None:
        target_id = str(original_event_id)
        self._last_voice_fusion_results = [
            updated
            if result.candidate_event is not None and str(result.candidate_event.id) == target_id
            else result
            for result in self._last_voice_fusion_results
        ]

    def _process_engine_event(self, event: ClinicalEvent, *, audit: bool = True) -> None:
        if event.status not in {EventStatus.ACCEPTED, EventStatus.CORRECTED}:
            raise ValueError("only accepted or corrected events may enter the engine")
        self._engine.process(event)
        self._timeline.append(event)
        if audit:
            self._audit(
                "engine_event_processed",
                {
                    "engine_eligible": True,
                    "event": event.model_dump(mode="json"),
                },
            )

    def _audit(self, record_type: str, payload: dict[str, Any]) -> None:
        if not self._audit_enabled:
            return
        self._audit_store.append(
            session_id=self.session_id,
            record_type=record_type,
            payload=payload,
        )

    def _audit_voice_response(self, response: DemoTranscriptResponse) -> None:
        self._audit(
            "voice_fusion_result",
            {
                "fusion_results": [
                    result.model_dump(mode="json") for result in response.fusion_results
                ],
                "confirmation_requests": [
                    request.model_dump(mode="json")
                    for request in response.confirmation_requests
                ],
                "evidence": [
                    evidence.model_dump(mode="json") for evidence in response.evidence
                ],
                "accepted_event_ids": response.accepted_event_ids,
            },
        )

    def _clear_pending_for(self, event_id: object) -> None:
        candidate_event_id = str(event_id)
        self._pending_fusion_results.pop(candidate_event_id, None)
        self._pending_confirmation_requests = {
            request_id: request
            for request_id, request in self._pending_confirmation_requests.items()
            if request.candidate_event_id != candidate_event_id
        }

    def _current_state_as_of(self, as_of: datetime) -> DemoStateResponse:
        rhythm_state = self._engine.get_machine_state("rhythm")
        cpr_state = self._engine.get_machine_state("cpr")
        shock_state = self._engine.get_machine_state("shocks")
        medication_state = self._engine.get_machine_state("medications")
        rosc_state = self._engine.get_machine_state("rosc")
        reversible_cause_state = self._engine.get_machine_state("reversible_causes")
        recommendations = self._owned_recommendations(as_of)
        coordinator_decision = self._coordinator.decide(
            WorkflowCoordinatorInput(
                machine_states={
                    "rhythm": rhythm_state,
                    "cpr": cpr_state,
                    "shocks": shock_state,
                    "medications": medication_state,
                    "rosc": rosc_state,
                    "reversible_causes": reversible_cause_state,
                },
                machine_recommendations=tuple(recommendations),
                accepted_event_timeline=tuple(self._timeline),
                safety_flags=tuple(self._safety_flags),
            )
        )
        visible = coordinator_decision.visible_state_summary
        cpr_elapsed = cpr_state.cycle_elapsed_seconds(as_of)
        hands_off_elapsed = cpr_state.hands_off_elapsed_seconds(as_of)
        return DemoStateResponse(
            current_workflow_phase=coordinator_decision.phase,
            current_rhythm=self._label(visible.rhythm),
            current_pathway=self._label(visible.pathway),
            cpr_status=self._label(visible.cpr_status),
            cpr_cycle_number=visible.cpr_cycle_number,
            cpr_cycle_elapsed_seconds=cpr_elapsed,
            cpr_hands_off_elapsed_seconds=hands_off_elapsed,
            shock_count=visible.shock_count,
            medication_history=self._medication_history(medication_state),
            rosc_status=self._label(visible.rosc_status),
            reversible_cause_state=reversible_cause_state,
            top_reversible_causes=list(visible.top_reversible_causes),
            primary_action=self._message(coordinator_decision.primary_action),
            secondary_actions=[
                item.recommendation.message for item in coordinator_decision.secondary_actions
            ],
            clinical_rationale=coordinator_decision.rationale,
            safety_flags=list(coordinator_decision.safety_flags),
            undoable_event_ids=self._active_undoable_event_ids(),
            rhythm_state=rhythm_state,
            cpr_state=cpr_state,
            shock_state=shock_state,
            medication_state=medication_state,
            rosc_state=rosc_state,
            recommendations=recommendations,
            coordinator_decision=coordinator_decision,
            timeline=[self._timeline_entry(event) for event in self._timeline],
        )

    def _owned_recommendations(self, as_of: datetime) -> list[OwnedRecommendation]:
        items: list[OwnedRecommendation] = []
        items.extend(
            self._wrap("rhythm", recommendation)
            for recommendation in self._rhythm_machine.get_recommendations()
        )
        items.extend(
            self._wrap("cpr", recommendation)
            for recommendation in self._cpr_machine.get_recommendations(as_of=as_of)
        )
        items.extend(
            self._wrap("shocks", recommendation)
            for recommendation in self._shock_machine.get_recommendations()
        )
        items.extend(
            self._wrap("medications", recommendation)
            for recommendation in self._medication_machine.get_recommendations(as_of=as_of)
        )
        items.extend(
            self._wrap("rosc", recommendation)
            for recommendation in self._rosc_machine.get_recommendations()
        )
        items.extend(
            self._wrap("reversible_causes", recommendation)
            for recommendation in self._reversible_cause_machine.get_recommendations()
        )
        return items

    def _wrap(self, owner: str, recommendation: Recommendation) -> OwnedRecommendation:
        return OwnedRecommendation(
            owner_machine=owner,
            action_kind=self._action_kind_for(recommendation.id),
            recommendation=recommendation,
        )

    def _event_for_action(self, action: DemoAction) -> ClinicalEvent:
        event_type, label, payload = self._event_spec(action)
        return ClinicalEvent(
            event_type=event_type,
            source=EventSource.SIMULATED,
            confidence=1.0,
            status=EventStatus.ACCEPTED,
            evidence=(
                Evidence(
                    source=EventSource.SIMULATED,
                    evidence_type="demo_button",
                    confidence=1.0,
                    payload={"action": action, **payload},
                    raw_reference=label,
                ),
            ),
            payload=payload,
            timestamp=datetime.now(UTC),
        )

    @staticmethod
    def _event_spec(action: DemoAction) -> tuple[EventType, str, dict[str, str | float]]:
        if action == "cpr_started":
            return EventType.CPR_STARTED, "CPR Started", {}
        if action == "cpr_paused":
            return EventType.CPR_PAUSED, "CPR Paused", {}
        if action == "cpr_resumed":
            return EventType.CPR_RESUMED, "CPR Resumed", {}
        if action == "vf":
            return EventType.RHYTHM_CHECKED, "VF", {"rhythm": "vf"}
        if action == "pvt":
            return EventType.RHYTHM_CHECKED, "pVT", {"rhythm": "pulseless_vt"}
        if action == "asystole":
            return EventType.RHYTHM_CHECKED, "Asystole", {"rhythm": "asystole"}
        if action == "pea":
            return EventType.RHYTHM_CHECKED, "PEA", {"rhythm": "pea"}
        if action == "shock_delivered":
            return EventType.SHOCK_DELIVERED, "Shock Delivered", {}
        if action == "epinephrine_given":
            return (
                EventType.MEDICATION_GIVEN,
                "Epinephrine Given",
                {"medication": "epinephrine", "dose": 1, "unit": "mg", "route": "IV/IO"},
            )
        if action == "amiodarone_given":
            return (
                EventType.MEDICATION_GIVEN,
                "Amiodarone Given",
                {"medication": "amiodarone", "dose": 300, "unit": "mg", "route": "IV/IO"},
            )
        return EventType.ROSC_ACHIEVED, "ROSC", {"rhythm": "rosc"}

    @staticmethod
    def _timeline_entry(event: ClinicalEvent) -> DemoTimelineEntry:
        label = str(event.evidence[0].raw_reference or event.event_type.value)
        return DemoTimelineEntry(
            id=str(event.id),
            timestamp=event.timestamp,
            event_type=event.event_type.value,
            label=label,
            payload={str(key): str(value) for key, value in event.payload.items()},
        )

    @staticmethod
    def _scenario_timeline_entry(
        *,
        transcript: str,
        response: DemoTranscriptResponse,
    ) -> DemoScenarioTimelineEntry:
        fusion_result = response.fusion_results[0] if response.fusion_results else None
        event = fusion_result.candidate_event if fusion_result is not None else None
        accepted_event = (
            DemoAcceptedEventSummary(
                id=str(event.id),
                event_type=event.event_type.value,
                confidence=event.confidence,
                status=event.status.value,
                payload={str(key): str(value) for key, value in event.payload.items()},
            )
            if event is not None and str(event.id) in response.accepted_event_ids
            else None
        )
        evidence = (
            [
                DemoWorkflowSession._evidence_summary(item)
                for item in event.evidence
            ]
            if event is not None
            else response.evidence
        )

        return DemoScenarioTimelineEntry(
            transcript=transcript,
            evidence=evidence,
            evidence_ids=fusion_result.evidence_ids if fusion_result is not None else [],
            result_kind=fusion_result.result_kind if fusion_result is not None else None,
            confidence=event.confidence if event is not None else None,
            fusion_decision=(
                event.status.value
                if event is not None
                else fusion_result.uncertainty_reason
                if fusion_result is not None
                else "no_observation"
            ),
            accepted_event=accepted_event,
            engine_state=DemoEngineStateSnapshot(
                rhythm=response.state.current_rhythm,
                pathway=response.state.current_pathway,
                shock_count=response.state.shock_count,
                medication_history=response.state.medication_history,
                rosc_status=response.state.rosc_status,
            ),
            recommendation=response.state.primary_action,
            secondary_recommendations=response.state.secondary_actions,
            rationale=response.state.clinical_rationale,
        )

    @staticmethod
    def _evidence_summary(item: Evidence) -> DemoEvidenceSummary:
        serialized = item.model_dump(mode="json")
        return DemoEvidenceSummary(
            id=str(item.id),
            source=item.source.value,
            evidence_type=item.evidence_type,
            confidence=item.confidence,
            raw_reference=item.raw_reference,
            payload=serialized["payload"],
        )

    @staticmethod
    def _action_kind_for(recommendation_id: str) -> ActionKind:
        mapping = {
            "rhythm.shockable.deliver_shock": ActionKind.DELIVER_SHOCK,
            "rhythm.unknown.assess_rhythm": ActionKind.CONFIRM_RHYTHM,
            "rhythm.organized.assess_rosc": ActionKind.CONFIRM_RHYTHM,
            "rhythm.non_shockable.cpr": ActionKind.CONTINUE_CPR,
            "rhythm.rosc.post_cardiac_arrest_care": ActionKind.TRANSITION_TO_POST_ARREST_CARE,
            "cpr.resume_cpr": ActionKind.RESUME_CPR,
            "cpr.continue_cpr": ActionKind.CONTINUE_CPR,
            "cpr.assess_rhythm": ActionKind.ASSESS_RHYTHM,
            "shocks.deliver_shock": ActionKind.DELIVER_SHOCK,
            "medications.give_epinephrine": ActionKind.GIVE_EPINEPHRINE,
            "medications.consider_amiodarone": ActionKind.CONSIDER_AMIODARONE,
            "medications.consider_lidocaine": ActionKind.CONSIDER_LIDOCAINE,
            "rosc.transition_to_post_arrest_care": ActionKind.TRANSITION_TO_POST_ARREST_CARE,
        }
        if recommendation_id.startswith("hs_ts.suggested_intervention."):
            return ActionKind.CONSIDER_REVERSIBLE_CAUSE
        return mapping[recommendation_id]

    @staticmethod
    def _message(action: OwnedRecommendation | None) -> str:
        if action is None:
            return "No action available."
        return action.recommendation.message

    @staticmethod
    def _medication_history(state: MedicationState) -> list[str]:
        history: list[str] = []
        for item in state.administrations:
            if item.dose is not None and item.unit is not None:
                history.append(f"{item.medication_name} {item.dose:g} {item.unit}")
            else:
                history.append(item.medication_name)
        return history

    @staticmethod
    def _label(value: object) -> str:
        text = str(value).replace("_", " ")
        return text.upper() if text in {"vf", "pea"} else text.title()


def _is_confirmable_voice_candidate(result: FusionResult) -> bool:
    event = result.candidate_event
    if event is None:
        return False
    if result.is_negative_evidence or result.result_kind == "negative_evidence":
        return False
    if result.uncertainty_reason == "conflicting_evidence":
        return False
    if not result.requires_confirmation or event.status != EventStatus.NEEDS_CONFIRMATION:
        return False
    return any(
        _observation_kind(item) in {
            "command",
            "intent",
            "completed_action",
            "observation",
            "rhythm_identification",
        }
        or item.evidence_type == "manual_confirmation"
        for item in event.evidence
    )


def _observation_kind(evidence: Evidence) -> str | None:
    value = evidence.payload.get("observation_kind")
    return str(value) if value is not None else None


def _event_observation_kind(event: ClinicalEvent) -> str | None:
    return next(
        (
            kind
            for evidence in event.evidence
            if (kind := _observation_kind(evidence)) is not None
        ),
        None,
    )


def _deduplication_window(event: ClinicalEvent) -> timedelta | None:
    kind = _event_observation_kind(event)
    if event.event_type == EventType.SHOCK_DELIVERED:
        return timedelta(seconds=15)
    if event.event_type in {EventType.CPR_STARTED, EventType.CPR_RESUMED}:
        return timedelta(seconds=10)
    if event.event_type == EventType.RHYTHM_CHECKED:
        return timedelta(seconds=10)
    if event.event_type == EventType.MEDICATION_GIVEN and kind == "completed_action":
        return timedelta(seconds=60)
    return None


def _is_closed_loop_auto_accepted(event: ClinicalEvent) -> bool:
    return _event_observation_kind(event) in {
        "completed_action",
        "rhythm_identification",
    }


def _completed_action_matches_pending_command(
    pending: ClinicalEvent,
    completed: ClinicalEvent,
) -> bool:
    if pending.event_type != completed.event_type:
        return False
    if dict(pending.payload) == dict(completed.payload):
        return True
    if pending.event_type != EventType.MEDICATION_GIVEN:
        return False
    return pending.payload.get("medication") == completed.payload.get("medication")


def _role_hypothesis_for_speaker(
    speaker_id: str | None,
    role_hypothesis: SpeakerRoleHypothesis | None,
) -> SpeakerRoleHypothesis | None:
    if role_hypothesis is None or speaker_id is None:
        return role_hypothesis
    if role_hypothesis.speaker_id == speaker_id:
        return role_hypothesis
    return role_hypothesis.model_copy(update={"speaker_id": speaker_id})


def _validated_correction_payload(
    request: DemoCorrectionActionRequest,
) -> tuple[EventType, dict[str, Any]]:
    if request.event_type is None:
        raise ValueError("correction event_type is required")
    if request.observation_kind != "completed_action":
        raise ValueError("correction requires explicit completed_action semantics")

    event_type = request.event_type
    payload = {str(key): value for key, value in request.payload.items()}
    if event_type == EventType.RHYTHM_CHECKED:
        rhythm = _normalized_payload_text(payload.get("rhythm"))
        allowed = {"vf", "pulseless_vt", "pea", "asystole", "rosc"}
        if rhythm not in allowed:
            raise ValueError("rhythm correction requires rhythm vf, pulseless_vt, pea, asystole, or rosc")
        return event_type, {"rhythm": rhythm}

    if event_type == EventType.MEDICATION_GIVEN:
        medication = _normalized_payload_text(payload.get("medication"))
        if medication not in {"epinephrine", "adrenaline", "amiodarone", "lidocaine"}:
            raise ValueError("medication correction requires a supported medication")
        normalized_medication = "epinephrine" if medication == "adrenaline" else medication
        corrected_payload: dict[str, Any] = {"medication": normalized_medication}
        if payload.get("dose") not in {None, ""}:
            try:
                corrected_payload["dose"] = float(payload["dose"])
            except (TypeError, ValueError) as exc:
                raise ValueError("medication correction dose must be numeric") from exc
            if corrected_payload["dose"].is_integer():
                corrected_payload["dose"] = int(corrected_payload["dose"])
            unit = _normalized_payload_text(payload.get("unit"))
            if unit != "mg":
                raise ValueError("medication correction dose requires unit mg")
            corrected_payload["unit"] = "mg"
        route = str(payload.get("route") or "").upper()
        if route:
            if route not in {"IV", "IO", "IV/IO"}:
                raise ValueError("medication correction route must be IV or IO")
            corrected_payload["route"] = route
        return event_type, corrected_payload

    if event_type == EventType.SHOCK_DELIVERED:
        return event_type, {}

    if event_type in {
        EventType.CPR_STARTED,
        EventType.CPR_RESUMED,
        EventType.CPR_PAUSED,
        EventType.ROSC_ACHIEVED,
    }:
        if event_type == EventType.ROSC_ACHIEVED:
            return event_type, {"rhythm": "rosc"}
        return event_type, {}

    raise ValueError(f"unsupported correction event_type: {event_type.value}")


def _normalized_payload_text(value: object) -> str:
    return str(value or "").casefold().strip().replace(" ", "_")


demo_session = DemoWorkflowSession()
