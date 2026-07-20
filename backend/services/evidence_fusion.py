from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, Field

from backend.audio.transcript_extractor import TranscriptPhraseEventExtractor
from backend.workflow.events import ClinicalEvent, EventStatus, EventType, Evidence


class FusionResult(BaseModel):
    candidate_event: ClinicalEvent | None = None
    requires_confirmation: bool = False
    evidence_ids: list[str] = Field(default_factory=list)
    uncertainty_reason: str | None = None
    policy_version: str = "phase2.deterministic.v1"
    result_kind: str = "candidate_event"
    is_negative_evidence: bool = False
    correction_target_event_type: EventType | None = None


class EvidenceFusionEngine(Protocol):
    def fuse(self, evidence: list[Evidence]) -> FusionResult:
        """Fuse observations into a candidate clinical event."""
        # TODO: Define deterministic fusion policy and confidence thresholds.
        ...


@dataclass(frozen=True)
class FusionThresholdPolicy:
    """Explicit confidence thresholds for deterministic Phase 2 fusion."""

    policy_version: str = "v0.4.high_impact_confirmation.v1"
    accept_threshold: float = 0.90
    confirm_threshold: float = 0.55
    independent_source_bonus: float = 0.08
    corroborating_evidence_bonus: float = 0.03


@dataclass(frozen=True)
class EvidenceInterpretation:
    event_type: EventType
    payload: Mapping[str, Any]
    confidence: float
    is_positive: bool = True
    observation_kind: str | None = None
    source: str | None = None
    evidence_type: str | None = None
    is_manual_confirmation: bool = False


_HIGH_IMPACT_EVENT_TYPES = {
    EventType.MEDICATION_GIVEN,
    EventType.SHOCK_DELIVERED,
    EventType.RHYTHM_CHECKED,
    EventType.ROSC_ACHIEVED,
}

_DEVICE_CORROBORATION_ALLOWLIST = {
    EventType.SHOCK_DELIVERED: {
        "defibrillator discharge",
        "defibrillator_discharge",
        "shock delivered",
        "shock_delivered",
    },
}


class DeterministicEvidenceFusionEngine:
    """Deterministic evidence-to-event fusion for Phase 2.

    Fusion does not call the workflow engine, state machines, AI, or UI. It
    proposes exactly one event for a compatible evidence set and assigns the
    event status according to the versioned threshold policy.
    """

    def __init__(
        self,
        *,
        policy: FusionThresholdPolicy | None = None,
        transcript_extractor: TranscriptPhraseEventExtractor | None = None,
    ) -> None:
        self._policy = policy or FusionThresholdPolicy()
        self._transcript_extractor = transcript_extractor or TranscriptPhraseEventExtractor()

    def fuse(self, evidence: list[Evidence]) -> FusionResult:
        if not evidence:
            return FusionResult(
                uncertainty_reason="no_evidence",
                policy_version=self._policy.policy_version,
            )

        evidence_tuple = tuple(evidence)
        evidence_ids = [str(item.id) for item in evidence_tuple]
        interpretations = tuple(
            interpretation
            for item in evidence_tuple
            if (interpretation := self._interpret(item)) is not None
        )

        if not interpretations:
            return FusionResult(
                candidate_event=None,
                evidence_ids=evidence_ids,
                uncertainty_reason="no_clinical_interpretation",
                policy_version=self._policy.policy_version,
                result_kind="no_clinical_interpretation",
            )

        if not any(item.is_positive for item in interpretations):
            return FusionResult(
                candidate_event=None,
                evidence_ids=evidence_ids,
                uncertainty_reason="negative_evidence_without_target",
                policy_version=self._policy.policy_version,
                result_kind="negative_evidence",
                is_negative_evidence=True,
                correction_target_event_type=_single_event_type(interpretations),
            )

        selected = self._select_interpretation(interpretations)
        if self._has_conflict(interpretations):
            event = self._event(
                selected,
                evidence_tuple,
                status=EventStatus.NEEDS_CONFIRMATION,
                confidence=self._combined_confidence(
                    self._acceptance_eligible_interpretations(interpretations)
                    or interpretations
                ),
            )
            return FusionResult(
                candidate_event=event,
                requires_confirmation=True,
                evidence_ids=evidence_ids,
                uncertainty_reason="conflicting_evidence",
                policy_version=self._policy.policy_version,
            )

        eligible_interpretations = self._acceptance_eligible_interpretations(
            interpretations
        )
        confidence = self._combined_confidence(
            eligible_interpretations or interpretations
        )
        status, requires_confirmation, uncertainty_reason = self._status_for(
            confidence,
            selected,
            eligible_interpretations,
        )
        event = self._event(
            selected,
            evidence_tuple,
            status=status,
            confidence=confidence,
        )
        return FusionResult(
            candidate_event=event,
            requires_confirmation=requires_confirmation,
            evidence_ids=evidence_ids,
            uncertainty_reason=uncertainty_reason,
            policy_version=self._policy.policy_version,
        )

    def _interpret(self, evidence: Evidence) -> EvidenceInterpretation | None:
        from_payload = self._interpret_structured_payload(evidence)
        if from_payload is not None:
            return from_payload

        if evidence.source.value == "speech":
            events = self._transcript_extractor.extract(evidence)
            if not events:
                return None
            event = events[0]
            return EvidenceInterpretation(
                event_type=event.event_type,
                payload=event.payload,
                confidence=event.confidence,
                is_positive=True,
                observation_kind=_optional_text(event.payload.get("extraction_kind")),
                source=evidence.source.value,
                evidence_type=evidence.evidence_type,
            )

        return self._interpret_raw_reference(evidence)

    @staticmethod
    def _interpret_structured_payload(evidence: Evidence) -> EvidenceInterpretation | None:
        raw_event_type = evidence.payload.get("event_type")
        if raw_event_type is None:
            return None

        event_type = EventType(str(raw_event_type))
        raw_payload = evidence.payload.get("payload")
        payload = dict(raw_payload) if isinstance(raw_payload, Mapping) else {}
        return EvidenceInterpretation(
            event_type=event_type,
            payload=payload,
            confidence=evidence.confidence,
            is_positive=bool(evidence.payload.get("is_positive", True)),
            observation_kind=_optional_text(
                evidence.payload.get("observation_kind")
                or payload.get("observation_kind")
                or payload.get("extraction_kind")
            ),
            source=evidence.source.value,
            evidence_type=evidence.evidence_type,
            is_manual_confirmation=_is_manual_confirmation_evidence(evidence),
        )

    @staticmethod
    def _interpret_raw_reference(evidence: Evidence) -> EvidenceInterpretation | None:
        text = _normalize(evidence.raw_reference or str(evidence.payload.get("label", "")))
        observation_type = _normalize(str(evidence.payload.get("observation_type", "")))

        if evidence.evidence_type == "manual_confirmation" and "no shock" in text:
            return EvidenceInterpretation(
                event_type=EventType.SHOCK_DELIVERED,
                payload={},
                confidence=evidence.confidence,
                is_positive=False,
                source=evidence.source.value,
                evidence_type=evidence.evidence_type,
                is_manual_confirmation=True,
            )
        if evidence.evidence_type == "manual_confirmation":
            rhythm = _rhythm_from_text(text)
            if rhythm is not None:
                return EvidenceInterpretation(
                    event_type=EventType.RHYTHM_CHECKED,
                    payload={"rhythm": rhythm},
                    confidence=evidence.confidence,
                    source=evidence.source.value,
                    evidence_type=evidence.evidence_type,
                    is_manual_confirmation=True,
                )

        if observation_type == "defibrillator discharge" and (
            evidence.source.value == "acoustic"
            or _is_allowed_device_source_for(EventType.SHOCK_DELIVERED, evidence)
        ):
            return EvidenceInterpretation(
                event_type=EventType.SHOCK_DELIVERED,
                payload={},
                confidence=evidence.confidence,
                source=evidence.source.value,
                evidence_type=evidence.evidence_type,
            )
        return None

    @staticmethod
    def _select_interpretation(
        interpretations: Sequence[EvidenceInterpretation],
    ) -> EvidenceInterpretation:
        for item in interpretations:
            if _is_acceptance_eligible(item):
                return item
        return interpretations[0]

    @staticmethod
    def _has_conflict(interpretations: Sequence[EvidenceInterpretation]) -> bool:
        selected = interpretations[0]
        for item in interpretations[1:]:
            if item.event_type != selected.event_type:
                return True
            if _clinical_payload(item.payload) != _clinical_payload(selected.payload):
                return True
            if item.is_positive != selected.is_positive:
                return True
        return not all(item.is_positive for item in interpretations)

    def _combined_confidence(
        self,
        interpretations: Sequence[EvidenceInterpretation],
    ) -> float:
        base = max(item.confidence for item in interpretations)
        source_count = len({item.source for item in interpretations})
        boost = max(0, source_count - 1) * self._policy.independent_source_bonus
        boost += max(0, len(interpretations) - 1) * self._policy.corroborating_evidence_bonus
        return round(min(1.0, base + boost), 4)

    @staticmethod
    def _acceptance_eligible_interpretations(
        interpretations: Sequence[EvidenceInterpretation],
    ) -> tuple[EvidenceInterpretation, ...]:
        selected = DeterministicEvidenceFusionEngine._select_interpretation(
            interpretations
        )
        return tuple(
            item
            for item in interpretations
            if _is_acceptance_eligible(item)
            and item.event_type == selected.event_type
            and _clinical_payload(item.payload) == _clinical_payload(selected.payload)
            and item.is_positive == selected.is_positive
        )

    def _status_for(
        self,
        confidence: float,
        interpretation: EvidenceInterpretation,
        eligible_interpretations: Sequence[EvidenceInterpretation],
    ) -> tuple[EventStatus, bool, str | None]:
        if interpretation.observation_kind in {"command", "intent"}:
            if confidence >= self._policy.confirm_threshold:
                return (
                    EventStatus.NEEDS_CONFIRMATION,
                    True,
                    f"{interpretation.observation_kind}_is_not_completed_action",
                )
            return (
                EventStatus.REJECTED,
                False,
                f"{interpretation.observation_kind}_confidence_below_confirmation_threshold",
            )
        if interpretation.observation_kind in {
            "completed_action",
            "rhythm_identification",
        }:
            if confidence >= self._policy.confirm_threshold:
                return EventStatus.ACCEPTED, False, "closed_loop_completion"
            return (
                EventStatus.REJECTED,
                False,
                f"{interpretation.observation_kind}_confidence_below_confirmation_threshold",
            )
        if interpretation.event_type in _HIGH_IMPACT_EVENT_TYPES:
            if confidence < self._policy.confirm_threshold:
                return (
                    EventStatus.REJECTED,
                    False,
                    "high_impact_confidence_below_confirmation_threshold",
                )
            if not _has_high_impact_acceptance_support(
                interpretation.event_type,
                eligible_interpretations,
            ):
                return (
                    EventStatus.NEEDS_CONFIRMATION,
                    True,
                    "high_impact_requires_corroboration",
                )
        if confidence >= self._policy.accept_threshold:
            return EventStatus.ACCEPTED, False, None
        if confidence >= self._policy.confirm_threshold:
            return (
                EventStatus.NEEDS_CONFIRMATION,
                True,
                "confidence_below_acceptance_threshold",
            )
        return EventStatus.REJECTED, False, "confidence_below_confirmation_threshold"

    @staticmethod
    def _event(
        interpretation: EvidenceInterpretation,
        evidence: Sequence[Evidence],
        *,
        status: EventStatus,
        confidence: float,
    ) -> ClinicalEvent:
        return ClinicalEvent(
            event_type=interpretation.event_type,
            source=evidence[0].source,
            confidence=confidence,
            status=status,
            evidence=tuple(evidence),
            payload=_clinical_payload(interpretation.payload),
            timestamp=min(item.timestamp for item in evidence),
        )


def _normalize(value: str) -> str:
    return " ".join(value.casefold().replace("-", " ").replace("_", " ").split())


def _rhythm_from_text(text: str) -> str | None:
    if "asystole" in text:
        return "asystole"
    if "pea" in text or "pulseless electrical activity" in text:
        return "pea"
    if "pulseless vt" in text or "pvt" in text:
        return "pulseless_vt"
    if "vf" in text or "ventricular fibrillation" in text:
        return "vf"
    if "rosc" in text:
        return "rosc"
    return None


def _clinical_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    ignored_keys = {"extraction_kind", "observation_kind"}
    return {
        str(key): value
        for key, value in payload.items()
        if key not in ignored_keys
    }


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _single_event_type(
    interpretations: Sequence[EvidenceInterpretation],
) -> EventType | None:
    event_types = {item.event_type for item in interpretations}
    if len(event_types) == 1:
        return next(iter(event_types))
    return None


def _is_acceptance_eligible(interpretation: EvidenceInterpretation) -> bool:
    return interpretation.observation_kind not in {"command", "intent"}


def _has_high_impact_acceptance_support(
    event_type: EventType,
    interpretations: Sequence[EvidenceInterpretation],
) -> bool:
    sources = {item.source for item in interpretations}
    if any(item.is_manual_confirmation for item in interpretations):
        return True
    if any(
        _is_allowed_device_interpretation_for(event_type, item)
        for item in interpretations
    ):
        return True
    if event_type == EventType.SHOCK_DELIVERED:
        return (
            any(item.source == "acoustic" for item in interpretations)
            and len(sources) > 1
        )
    return False


def _is_manual_confirmation_evidence(evidence: Evidence) -> bool:
    if evidence.source.value != "manual":
        return False
    if evidence.evidence_type == "manual_confirmation":
        return True
    return bool(
        evidence.payload.get("is_confirmation")
        or evidence.payload.get("confirmed")
        or evidence.payload.get("manual_confirmation")
    )


def _is_allowed_device_source_for(event_type: EventType, evidence: Evidence) -> bool:
    if evidence.source.value != "device_future":
        return False
    allowed = _DEVICE_CORROBORATION_ALLOWLIST.get(event_type, set())
    evidence_labels = {
        _normalize(evidence.evidence_type),
        _normalize(str(evidence.payload.get("observation_type", ""))),
        _normalize(str(evidence.payload.get("device_event_type", ""))),
    }
    return bool(allowed.intersection(evidence_labels))


def _is_allowed_device_interpretation_for(
    event_type: EventType,
    interpretation: EvidenceInterpretation,
) -> bool:
    if interpretation.source != "device_future":
        return False
    allowed = _DEVICE_CORROBORATION_ALLOWLIST.get(event_type, set())
    return _normalize(interpretation.evidence_type or "") in allowed
