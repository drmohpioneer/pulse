from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, Field

from backend.audio.multimodal import (
    AcousticObservation,
    AcousticObservationPlaceholder,
    AudioChunk,
    DeterministicMedicalPhraseNormalizer,
    DiarizationPlaceholder,
    MultilingualTranscriptSegment,
    NormalizedClinicalObservation,
    SpeakerRoleHypothesis,
    SpeakerTurn,
    TranscriptLanguage,
    VoiceActivitySegment,
    acoustic_observation_to_evidence,
    clinical_observation_to_evidence,
)
from backend.services.confirmation import ConfirmationOption, ConfirmationRequest
from backend.services.evidence_fusion import DeterministicEvidenceFusionEngine, FusionResult
from backend.workflow.events import ClinicalEvent, EventSource, EventStatus, EventType, Evidence


_HIGH_IMPACT_EVENT_TYPES = {
    EventType.MEDICATION_GIVEN,
    EventType.SHOCK_DELIVERED,
    EventType.RHYTHM_CHECKED,
    EventType.ROSC_ACHIEVED,
}
_ACTIVE_ARREST_RHYTHMS = {"vf", "pulseless_vt", "pea", "asystole"}


class MultimodalTranscriptIngestRequest(BaseModel):
    text: str
    confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    language: TranscriptLanguage = TranscriptLanguage.UNKNOWN
    speaker_id: str | None = None
    diarization_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    is_overlapping: bool = False
    role_hypothesis: SpeakerRoleHypothesis | None = None
    timestamp: datetime | None = None
    session_id: str = "demo-session"
    device_id: str | None = None
    acoustic_observations: tuple[AcousticObservation, ...] = Field(default_factory=tuple)


class MultimodalPerceptionResult(BaseModel):
    chunk: AudioChunk
    voice_activity_segments: tuple[VoiceActivitySegment, ...]
    speaker_turns: tuple[SpeakerTurn, ...]
    transcript_segments: tuple[MultilingualTranscriptSegment, ...]
    normalized_observations: tuple[NormalizedClinicalObservation, ...]
    acoustic_observations: tuple[AcousticObservation, ...]
    evidence: tuple[Evidence, ...]
    evidence_groups: tuple[tuple[Evidence, ...], ...]
    fusion_results: tuple[FusionResult, ...]
    confirmation_requests: tuple[ConfirmationRequest, ...]

    @property
    def accepted_events(self) -> tuple[ClinicalEvent, ...]:
        return tuple(
            result.candidate_event
            for result in self.fusion_results
            if result.candidate_event is not None
            and result.candidate_event.status in {EventStatus.ACCEPTED, EventStatus.CORRECTED}
        )


@dataclass(frozen=True)
class MultimodalEvidenceGroupingPolicy:
    policy_version: str = "v0.4.multimodal_grouping.v1"
    default_window_seconds: int = 5
    shock_window_seconds: int = 10

    def window_for(self, event_type: EventType) -> timedelta:
        if event_type == EventType.SHOCK_DELIVERED:
            return timedelta(seconds=self.shock_window_seconds)
        return timedelta(seconds=self.default_window_seconds)


@dataclass(frozen=True)
class GroupableEvidence:
    evidence: Evidence
    event_type: EventType
    payload: Mapping[str, Any]
    is_positive: bool = True

    @property
    def key(self) -> tuple[EventType, str]:
        return self.event_type, _payload_key(self.payload)


class DeterministicMultimodalVoicePipeline:
    """Minimal v0.4 multimodal perception slice.

    This service simulates transcript ingestion through the provider-neutral
    perception contracts, converts observations to Evidence, and delegates
    candidate event policy to the existing deterministic fusion engine.
    """

    def __init__(
        self,
        *,
        diarizer: DiarizationPlaceholder | None = None,
        normalizer: DeterministicMedicalPhraseNormalizer | None = None,
        acoustic_detector: AcousticObservationPlaceholder | None = None,
        fusion_engine: DeterministicEvidenceFusionEngine | None = None,
        grouping_policy: MultimodalEvidenceGroupingPolicy | None = None,
    ) -> None:
        self._diarizer = diarizer or DiarizationPlaceholder()
        self._normalizer = normalizer or DeterministicMedicalPhraseNormalizer()
        self._acoustic_detector = acoustic_detector or AcousticObservationPlaceholder()
        self._fusion_engine = fusion_engine or DeterministicEvidenceFusionEngine()
        self._grouping_policy = grouping_policy or MultimodalEvidenceGroupingPolicy()

    def ingest_transcript(
        self,
        request: MultimodalTranscriptIngestRequest,
    ) -> MultimodalPerceptionResult:
        timestamp = request.timestamp or datetime.now(UTC)
        chunk = AudioChunk(
            session_id=request.session_id,
            device_id=request.device_id,
            started_at=timestamp,
            ended_at=timestamp + timedelta(seconds=1),
            simulated_text=request.text,
            metadata={
                "speaker_id": request.speaker_id or "speaker_unknown",
                "speech_confidence": request.confidence,
                "diarization_confidence": request.diarization_confidence,
                "overlap_probability": 0.5 if request.is_overlapping else 0.0,
            },
        )
        voice_segments = self._diarizer.segment(chunk)
        speaker_turns = self._diarizer.diarize(chunk)
        turn = speaker_turns[0]
        if request.diarization_confidence is not None or request.is_overlapping:
            turn = turn.model_copy(
                update={
                    "diarization_confidence": (
                        request.diarization_confidence
                        if request.diarization_confidence is not None
                        else turn.diarization_confidence
                    ),
                    "is_overlapping": request.is_overlapping,
                }
            )
            speaker_turns = (turn, *speaker_turns[1:])
        transcript = MultilingualTranscriptSegment(
            turn_id=turn.id,
            speaker_id=turn.speaker_id,
            text=request.text,
            language=request.language,
            confidence=request.confidence,
            started_at=timestamp,
            ended_at=chunk.ended_at,
            role_hypothesis=request.role_hypothesis,
        )
        observations = self._normalizer.normalize(transcript)
        acoustic_observations = (
            request.acoustic_observations or self._acoustic_detector.detect(chunk)
        )
        evidence = tuple(
            clinical_observation_to_evidence(observation)
            for observation in observations
        ) + tuple(
            acoustic_observation_to_evidence(observation)
            for observation in acoustic_observations
        )
        evidence_groups = self.group_evidence(evidence)
        fusion_results = self.fuse_evidence_groups(evidence_groups)
        confirmations = tuple(
            self._confirmation_request(result)
            for result in fusion_results
            if result.requires_confirmation and result.candidate_event is not None
        )

        return MultimodalPerceptionResult(
            chunk=chunk,
            voice_activity_segments=voice_segments,
            speaker_turns=speaker_turns,
            transcript_segments=(transcript,),
            normalized_observations=observations,
            acoustic_observations=acoustic_observations,
            evidence=evidence,
            evidence_groups=evidence_groups,
            fusion_results=fusion_results,
            confirmation_requests=confirmations,
        )

    def group_evidence(
        self,
        evidence: Sequence[Evidence],
    ) -> tuple[tuple[Evidence, ...], ...]:
        interpreted = tuple(
            item
            for evidence_item in evidence
            if (item := _groupable_evidence(evidence_item)) is not None
        )
        interpreted_by_id = {item.evidence.id: item for item in interpreted}
        groups: list[list[GroupableEvidence]] = []

        for item in sorted(
            interpreted,
            key=lambda candidate: (
                candidate.evidence.timestamp,
                str(candidate.evidence.id),
            ),
        ):
            for group in groups:
                if not _inside_group_window(
                    item,
                    group,
                    self._grouping_policy,
                ):
                    continue
                if item.key == group[0].key or _conflicts_with_group(item, group):
                    group.append(item)
                    break
            else:
                groups.append([item])

        grouped_ids = {
            item.evidence.id
            for group in groups
            for item in group
        }
        evidence_groups = [
            tuple(item.evidence for item in group)
            for group in groups
        ]

        for evidence_item in sorted(
            evidence,
            key=lambda item: (item.timestamp, str(item.id)),
        ):
            if evidence_item.id in grouped_ids:
                continue
            if evidence_item.id not in interpreted_by_id:
                evidence_groups.append((evidence_item,))

        return tuple(evidence_groups)

    def fuse_evidence_groups(
        self,
        evidence_groups: Sequence[Sequence[Evidence]],
    ) -> tuple[FusionResult, ...]:
        return tuple(
            self._fusion_engine.fuse(list(group))
            for group in evidence_groups
        )

    @staticmethod
    def _confirmation_request(result: FusionResult) -> ConfirmationRequest:
        event = result.candidate_event
        if event is None:
            raise ValueError("confirmation requests require a candidate event")
        request_id = uuid5(
            NAMESPACE_URL,
            f"pulse-v04-confirmation:{event.id}:{result.policy_version}:{result.uncertainty_reason}",
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


def _groupable_evidence(evidence: Evidence) -> GroupableEvidence | None:
    from_structured = _structured_groupable_evidence(evidence)
    if from_structured is not None:
        return from_structured

    if evidence.evidence_type == "manual_confirmation":
        text = _normalize_text(
            evidence.raw_reference or str(evidence.payload.get("label", ""))
        )
        if "no shock" in text:
            return GroupableEvidence(
                evidence=evidence,
                event_type=EventType.SHOCK_DELIVERED,
                payload={},
                is_positive=False,
            )
        if "shock" in text:
            return GroupableEvidence(
                evidence=evidence,
                event_type=EventType.SHOCK_DELIVERED,
                payload={},
            )

    observation_type = _normalize_text(
        str(evidence.payload.get("observation_type", ""))
    )
    if (
        evidence.source == EventSource.ACOUSTIC
        and observation_type == "defibrillator discharge"
    ):
        return GroupableEvidence(
            evidence=evidence,
            event_type=EventType.SHOCK_DELIVERED,
            payload={},
        )

    return None


def _inside_group_window(
    item: GroupableEvidence,
    group: Sequence[GroupableEvidence],
    policy: MultimodalEvidenceGroupingPolicy,
) -> bool:
    first = group[0]
    window = max(
        (policy.window_for(member.event_type) for member in (*group, item)),
        default=policy.window_for(first.event_type),
    )
    return item.evidence.timestamp - first.evidence.timestamp <= window


def _conflicts_with_group(
    item: GroupableEvidence,
    group: Sequence[GroupableEvidence],
) -> bool:
    if item.event_type not in _HIGH_IMPACT_EVENT_TYPES:
        return False
    return any(_high_impact_payloads_conflict(item, member) for member in group)


def _high_impact_payloads_conflict(
    left: GroupableEvidence,
    right: GroupableEvidence,
) -> bool:
    if left.event_type not in _HIGH_IMPACT_EVENT_TYPES:
        return False
    if right.event_type not in _HIGH_IMPACT_EVENT_TYPES:
        return False
    if left.event_type == right.event_type:
        if left.is_positive != right.is_positive:
            return True
        return _same_event_payloads_conflict(
            left.event_type,
            left.payload,
            right.payload,
        )
    return _cross_event_payloads_conflict(left, right)


def _same_event_payloads_conflict(
    event_type: EventType,
    left_payload: Mapping[str, Any],
    right_payload: Mapping[str, Any],
) -> bool:
    if event_type == EventType.RHYTHM_CHECKED:
        left_rhythm = _normalize_text(str(left_payload.get("rhythm", "")))
        right_rhythm = _normalize_text(str(right_payload.get("rhythm", "")))
        return bool(left_rhythm and right_rhythm and left_rhythm != right_rhythm)
    if event_type == EventType.MEDICATION_GIVEN:
        left_medication = _normalize_text(str(left_payload.get("medication", "")))
        right_medication = _normalize_text(str(right_payload.get("medication", "")))
        if not left_medication or left_medication != right_medication:
            return False
        return _payload_key(left_payload) != _payload_key(right_payload)
    if event_type == EventType.ROSC_ACHIEVED:
        return _payload_key(left_payload) != _payload_key(right_payload)
    return False


def _cross_event_payloads_conflict(
    left: GroupableEvidence,
    right: GroupableEvidence,
) -> bool:
    event_types = {left.event_type, right.event_type}
    if event_types != {EventType.ROSC_ACHIEVED, EventType.RHYTHM_CHECKED}:
        return False
    rhythm_item = left if left.event_type == EventType.RHYTHM_CHECKED else right
    rhythm = _normalize_text(str(rhythm_item.payload.get("rhythm", "")))
    return rhythm in _ACTIVE_ARREST_RHYTHMS


def _structured_groupable_evidence(evidence: Evidence) -> GroupableEvidence | None:
    raw_event_type = evidence.payload.get("event_type")
    if raw_event_type is None:
        return None

    raw_payload = evidence.payload.get("payload")
    payload = dict(raw_payload) if isinstance(raw_payload, Mapping) else {}
    return GroupableEvidence(
        evidence=evidence,
        event_type=EventType(str(raw_event_type)),
        payload=_clinical_payload(payload),
        is_positive=bool(evidence.payload.get("is_positive", True)),
    )


def _clinical_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    ignored_keys = {"extraction_kind", "observation_kind"}
    return {str(key): value for key, value in payload.items() if key not in ignored_keys}


def _payload_key(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        _clinical_payload(payload),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _normalize_text(value: str) -> str:
    return " ".join(value.casefold().replace("-", " ").replace("_", " ").split())
