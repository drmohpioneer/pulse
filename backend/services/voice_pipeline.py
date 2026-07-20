from datetime import UTC, datetime
from uuid import uuid5, NAMESPACE_URL

from pydantic import BaseModel, Field

from backend.audio.transcription import ACLSTranscriptEventExtractor, TranscriptSegment
from backend.services.confirmation import ConfirmationOption, ConfirmationRequest
from backend.services.evidence_fusion import DeterministicEvidenceFusionEngine, FusionResult
from backend.workflow.events import ClinicalEvent, EventSource, EventStatus, Evidence


class TranscriptIngestRequest(BaseModel):
    text: str
    confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    speaker_label: str | None = None
    timestamp: datetime | None = None


class VoicePipelineResult(BaseModel):
    segment: TranscriptSegment
    fusion_results: list[FusionResult]
    confirmation_requests: list[ConfirmationRequest]

    @property
    def accepted_events(self) -> list[ClinicalEvent]:
        return [
            result.candidate_event
            for result in self.fusion_results
            if result.candidate_event is not None
            and result.candidate_event.status in {EventStatus.ACCEPTED, EventStatus.CORRECTED}
        ]


class DeterministicVoiceEvidencePipeline:
    """Simulated transcript to fused clinical events for the Phase 2 demo."""

    def __init__(
        self,
        *,
        extractor: ACLSTranscriptEventExtractor | None = None,
        fusion_engine: DeterministicEvidenceFusionEngine | None = None,
    ) -> None:
        self._extractor = extractor or ACLSTranscriptEventExtractor()
        self._fusion_engine = fusion_engine or DeterministicEvidenceFusionEngine()

    def ingest_transcript(self, request: TranscriptIngestRequest) -> VoicePipelineResult:
        segment = TranscriptSegment(
            text=request.text,
            confidence=request.confidence,
            timestamp=request.timestamp or datetime.now(UTC),
            speaker_label=request.speaker_label,
        )
        fusion_results: list[FusionResult] = []

        for observation in self._extractor.extract(segment):
            evidence = Evidence(
                source=EventSource.SPEECH,
                evidence_type="transcript_observation",
                timestamp=observation.timestamp,
                confidence=observation.confidence,
                payload={
                    "event_type": observation.event_type.value,
                    "payload": dict(observation.payload),
                    "observation_kind": observation.observation_kind.value,
                    "speaker_label": observation.speaker_label,
                    "text": observation.raw_text,
                },
                raw_reference=observation.raw_text,
            )
            fusion_results.append(self._fusion_engine.fuse([evidence]))

        return VoicePipelineResult(
            segment=segment,
            fusion_results=fusion_results,
            confirmation_requests=[
                self._confirmation_request(result)
                for result in fusion_results
                if result.requires_confirmation and result.candidate_event is not None
            ],
        )

    @staticmethod
    def _confirmation_request(result: FusionResult) -> ConfirmationRequest:
        event = result.candidate_event
        if event is None:
            raise ValueError("confirmation requests require a candidate event")

        request_id = uuid5(
            NAMESPACE_URL,
            f"pulse-confirmation:{event.id}:{result.policy_version}:{result.uncertainty_reason}",
        )
        return ConfirmationRequest(
            id=str(request_id),
            candidate_event_id=str(event.id),
            reason=result.uncertainty_reason or "requires_confirmation",
            confidence=event.confidence,
            options=[
                ConfirmationOption.CONFIRM,
                ConfirmationOption.REJECT,
                ConfirmationOption.CORRECT,
            ],
        )
