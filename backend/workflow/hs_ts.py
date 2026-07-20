from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from backend.models.common import Explanation
from backend.workflow.base import BaseWorkflowStateMachine
from backend.workflow.events import ClinicalEvent, EventStatus, EventType
from backend.workflow.recommendations import Recommendation, RecommendationPriority


class ReversibleCauseStatus(StrEnum):
    UNKNOWN = "unknown"
    CONSIDERED = "considered"
    SUSPECTED = "suspected"
    ADDRESSED = "addressed"
    TODO = "todo"


class ReversibleCause(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    status: ReversibleCauseStatus = ReversibleCauseStatus.UNKNOWN
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    suggested_intervention: str | None = None
    event_ids: tuple[str, ...] = Field(default_factory=tuple)


class ReversibleCauseState(BaseModel):
    model_config = ConfigDict(frozen=True)

    causes: tuple[ReversibleCause, ...] = Field(default_factory=tuple)
    possible_cause: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    suggested_intervention: str | None = None


class ReversibleCauseStateMachine(BaseWorkflowStateMachine[ReversibleCauseState]):
    """Deterministic reversible-causes (H's & T's) state machine.

    This machine only consumes accepted/corrected
    ``REVERSIBLE_CAUSE_CONSIDERED`` events. It does not diagnose, call AI, read
    other machines, or infer events. It ranks already-accepted reversible-cause
    evidence deterministically so replay produces the same state every time.
    """

    def _initial_state(self) -> ReversibleCauseState:
        return ReversibleCauseState()

    def apply_event(self, event: ClinicalEvent) -> ReversibleCauseState:
        """Apply an accepted event to reversible cause state."""
        if event.status not in {EventStatus.ACCEPTED, EventStatus.CORRECTED}:
            return self.get_state()
        if event.event_type != EventType.REVERSIBLE_CAUSE_CONSIDERED:
            return self.get_state()

        cause_name = _normalize_text(event.payload.get("cause"))
        if cause_name is None:
            return self.get_state()

        confidence = _payload_confidence(event)
        suggested_intervention = _normalize_text(event.payload.get("suggested_intervention"))
        evidence_ids = _evidence_ids(event)
        event_id = str(event.id)
        existing_by_key = {_cause_key(cause.name): cause for cause in self._state.causes}
        existing = existing_by_key.get(_cause_key(cause_name))

        if existing is None:
            updated = ReversibleCause(
                name=cause_name,
                status=ReversibleCauseStatus.SUSPECTED,
                confidence=confidence,
                evidence_ids=evidence_ids,
                suggested_intervention=suggested_intervention,
                event_ids=(event_id,),
            )
        else:
            keep_existing_intervention = (
                existing.suggested_intervention is not None
                and (confidence < existing.confidence or suggested_intervention is None)
            )
            updated = existing.model_copy(
                update={
                    "status": ReversibleCauseStatus.SUSPECTED,
                    "confidence": max(existing.confidence, confidence),
                    "evidence_ids": _unique(existing.evidence_ids + evidence_ids),
                    "suggested_intervention": (
                        existing.suggested_intervention
                        if keep_existing_intervention
                        else suggested_intervention
                    ),
                    "event_ids": _unique(existing.event_ids + (event_id,)),
                },
                deep=True,
            )

        causes = tuple(
            sorted(
                (
                    updated
                    if _cause_key(cause.name) == _cause_key(cause_name)
                    else cause
                    for cause in (*self._state.causes, updated)
                ),
                key=lambda cause: _cause_key(cause.name),
            )
        )
        causes = _dedupe_causes(causes)
        self._state = _state_from_causes(causes)
        return self.get_state()

    def get_recommendations(self) -> list[Recommendation]:
        """Return deterministic next actions from reversible cause state."""
        recommendations: list[Recommendation] = []
        for cause in _rank_causes(self._state.causes):
            if cause.suggested_intervention is None:
                continue
            recommendations.append(
                Recommendation(
                    id=f"hs_ts.suggested_intervention.{_action_key(cause.name)}",
                    priority=_priority_for_confidence(cause.confidence),
                    message=f"Consider {cause.suggested_intervention}.",
                    rationale=(
                        f"{cause.name} has accepted reversible-cause evidence "
                        f"with confidence {cause.confidence:.2f}."
                    ),
                    referenced_state_fields=[
                        "causes",
                        "possible_cause",
                        "confidence",
                        "evidence_ids",
                        "suggested_intervention",
                    ],
                    requires_confirmation=True,
                )
            )
        return recommendations

    def explain(self) -> Explanation:
        """Explain the current reversible cause state."""
        if self._state.possible_cause is None:
            return Explanation(
                summary="No accepted reversible-cause evidence has been recorded.",
                referenced_event_ids=[],
                referenced_state_fields=["causes"],
                metadata={"machine": "ReversibleCauseStateMachine"},
            )

        event_ids = tuple(
            event_id
            for cause in _rank_causes(self._state.causes)
            for event_id in cause.event_ids
        )
        return Explanation(
            summary=(
                f"Top reversible cause is {self._state.possible_cause} "
                f"with confidence {self._state.confidence:.2f}."
            ),
            referenced_event_ids=list(_unique(event_ids)),
            referenced_state_fields=[
                "causes",
                "possible_cause",
                "confidence",
                "evidence_ids",
                "suggested_intervention",
            ],
            metadata={
                "machine": "ReversibleCauseStateMachine",
                "evidence_ids": list(self._state.evidence_ids),
            },
        )

    def replay(self, events) -> ReversibleCauseState:
        """Replay active events in caller-supplied order, skipping superseded ones."""
        ordered_events = tuple(events)
        superseded_ids = {
            event.supersedes_event_id
            for event in ordered_events
            if event.supersedes_event_id is not None
        }
        self.reset()
        for event in ordered_events:
            if event.id in superseded_ids:
                continue
            self.apply_event(event)
        return self.get_state()


def _normalize_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).strip().split())
    return normalized or None


def _cause_key(value: str) -> str:
    return " ".join(value.casefold().split())


def _action_key(value: str) -> str:
    return _cause_key(value).replace(" ", "_").replace("/", "_")


def _payload_confidence(event: ClinicalEvent) -> float:
    value = event.payload.get("confidence", event.confidence)
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = event.confidence
    return min(1.0, max(0.0, confidence))


def _evidence_ids(event: ClinicalEvent) -> tuple[str, ...]:
    payload_evidence = event.payload.get("evidence")
    evidence_ids: list[str] = []
    if isinstance(payload_evidence, str):
        evidence_ids.append(payload_evidence)
    elif isinstance(payload_evidence, Mapping):
        evidence_id = payload_evidence.get("id")
        if evidence_id is not None:
            evidence_ids.append(str(evidence_id))
    elif isinstance(payload_evidence, tuple | list):
        for item in payload_evidence:
            if isinstance(item, Mapping):
                evidence_id = item.get("id")
                if evidence_id is not None:
                    evidence_ids.append(str(evidence_id))
            else:
                evidence_ids.append(str(item))

    if not evidence_ids:
        evidence_ids.extend(str(evidence.id) for evidence in event.evidence)
    return _unique(tuple(evidence_ids))


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    unique_values: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique_values.append(value)
    return tuple(unique_values)


def _dedupe_causes(causes: tuple[ReversibleCause, ...]) -> tuple[ReversibleCause, ...]:
    deduped: dict[str, ReversibleCause] = {}
    for cause in causes:
        deduped[_cause_key(cause.name)] = cause
    return tuple(deduped[key] for key in sorted(deduped))


def _rank_causes(causes: tuple[ReversibleCause, ...]) -> tuple[ReversibleCause, ...]:
    return tuple(
        sorted(
            causes,
            key=lambda cause: (-cause.confidence, _cause_key(cause.name)),
        )
    )


def _state_from_causes(causes: tuple[ReversibleCause, ...]) -> ReversibleCauseState:
    ranked_causes = _rank_causes(causes)
    if not ranked_causes:
        return ReversibleCauseState()

    top_cause = ranked_causes[0]
    return ReversibleCauseState(
        causes=causes,
        possible_cause=top_cause.name,
        confidence=top_cause.confidence,
        evidence_ids=top_cause.evidence_ids,
        suggested_intervention=top_cause.suggested_intervention,
    )


def _priority_for_confidence(confidence: float) -> RecommendationPriority:
    if confidence >= 0.8:
        return RecommendationPriority.HIGH
    if confidence >= 0.5:
        return RecommendationPriority.MEDIUM
    return RecommendationPriority.LOW
