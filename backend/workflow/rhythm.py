from collections.abc import Iterable, Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from backend.models.common import Explanation
from backend.workflow.base import BaseWorkflowStateMachine
from backend.workflow.events import ClinicalEvent, EventStatus, EventType
from backend.workflow.recommendations import Recommendation, RecommendationPriority


class RhythmCategory(StrEnum):
    UNKNOWN = "unknown"
    SHOCKABLE = "shockable"
    NON_SHOCKABLE = "non_shockable"
    ORGANIZED = "organized"
    ROSC = "rosc"


class RhythmName(StrEnum):
    UNKNOWN = "unknown"
    SHOCKABLE_UNKNOWN = "shockable_unknown"
    NON_SHOCKABLE_UNKNOWN = "non_shockable_unknown"
    VF = "vf"
    PULSELESS_VT = "pulseless_vt"
    PEA = "pea"
    ASYSTOLE = "asystole"
    ORGANIZED = "organized"
    ROSC = "rosc"


class RhythmState(BaseModel):
    model_config = ConfigDict(frozen=True)

    current_rhythm: RhythmName = RhythmName.UNKNOWN
    current_category: RhythmCategory = RhythmCategory.UNKNOWN
    last_checked_at_event_id: UUID | None = None
    last_checked_at: datetime | None = None
    confidence: float | None = None
    applied_event_ids: tuple[UUID, ...] = Field(default_factory=tuple)
    last_transition_reason: str = "No accepted rhythm event has been applied."


_RHYTHM_ALIASES: Mapping[str, RhythmName] = {
    "vf": RhythmName.VF,
    "ventricular_fibrillation": RhythmName.VF,
    "ventricular fibrillation": RhythmName.VF,
    "pvt": RhythmName.PULSELESS_VT,
    "pulseless_vt": RhythmName.PULSELESS_VT,
    "pulseless vt": RhythmName.PULSELESS_VT,
    "pulseless_ventricular_tachycardia": RhythmName.PULSELESS_VT,
    "pulseless ventricular tachycardia": RhythmName.PULSELESS_VT,
    "pea": RhythmName.PEA,
    "pulseless_electrical_activity": RhythmName.PEA,
    "pulseless electrical activity": RhythmName.PEA,
    "asystole": RhythmName.ASYSTOLE,
    "organized": RhythmName.ORGANIZED,
    "organized_rhythm": RhythmName.ORGANIZED,
    "organized rhythm": RhythmName.ORGANIZED,
    "rosc": RhythmName.ROSC,
    "return_of_spontaneous_circulation": RhythmName.ROSC,
    "return of spontaneous circulation": RhythmName.ROSC,
    "shockable": RhythmName.SHOCKABLE_UNKNOWN,
    "non_shockable": RhythmName.NON_SHOCKABLE_UNKNOWN,
    "nonshockable": RhythmName.NON_SHOCKABLE_UNKNOWN,
    "unknown": RhythmName.UNKNOWN,
}

_ACCEPTED_STATUSES = {EventStatus.ACCEPTED, EventStatus.CORRECTED}


class RhythmStateMachine(BaseWorkflowStateMachine[RhythmState]):
    """Deterministic rhythm state machine for adult cardiac arrest rhythm class.

    This machine classifies rhythm events into the official adult cardiac arrest
    pathway branches: shockable VF/pVT, nonshockable Asystole/PEA, ROSC, or
    unknown/organized rhythm requiring clinical assessment.
    """

    def _initial_state(self) -> RhythmState:
        return RhythmState()

    def apply_event(self, event: ClinicalEvent) -> RhythmState:
        """Apply an accepted rhythm-related event to rhythm state."""
        if event.status not in _ACCEPTED_STATUSES:
            return self.get_state()

        if event.event_type == EventType.ROSC_ACHIEVED:
            return self._transition(event, RhythmName.ROSC)

        if event.event_type != EventType.RHYTHM_CHECKED:
            return self.get_state()

        rhythm = self._rhythm_from_payload(event.payload)
        return self._transition(event, rhythm)

    def get_recommendations(self) -> list[Recommendation]:
        """Return deterministic rhythm-driven next actions."""
        state = self.get_state()

        if state.current_category == RhythmCategory.SHOCKABLE:
            return [
                Recommendation(
                    id="rhythm.shockable.deliver_shock",
                    priority=RecommendationPriority.CRITICAL,
                    message="Deliver shock.",
                    rationale="VF/pVT is a shockable cardiac arrest rhythm in the adult cardiac arrest algorithm.",
                    referenced_state_fields=["current_rhythm", "current_category"],
                )
            ]

        if state.current_category == RhythmCategory.NON_SHOCKABLE:
            return [
                Recommendation(
                    id="rhythm.non_shockable.cpr",
                    priority=RecommendationPriority.CRITICAL,
                    message="Continue CPR for 2 minutes.",
                    rationale="Asystole/PEA follows the nonshockable cardiac arrest pathway.",
                    referenced_state_fields=["current_rhythm", "current_category"],
                )
            ]

        if state.current_category == RhythmCategory.ROSC:
            return [
                Recommendation(
                    id="rhythm.rosc.post_cardiac_arrest_care",
                    priority=RecommendationPriority.CRITICAL,
                    message="Transition to post-cardiac arrest care.",
                    rationale="ROSC routes out of the cardiac arrest algorithm to post-cardiac arrest care.",
                    referenced_state_fields=["current_rhythm", "current_category"],
                )
            ]

        if state.current_category == RhythmCategory.ORGANIZED:
            return [
                Recommendation(
                    id="rhythm.organized.assess_rosc",
                    priority=RecommendationPriority.HIGH,
                    message="Assess for signs of ROSC.",
                    rationale="An organized rhythm during arrest requires assessment for ROSC before pathway selection.",
                    referenced_state_fields=["current_rhythm", "current_category"],
                    requires_confirmation=True,
                )
            ]

        return [
            Recommendation(
                id="rhythm.unknown.assess_rhythm",
                priority=RecommendationPriority.HIGH,
                message="Assess rhythm and determine whether it is shockable.",
                rationale="The adult cardiac arrest algorithm branches on whether the rhythm is shockable.",
                referenced_state_fields=["current_rhythm", "current_category"],
                requires_confirmation=True,
            )
        ]

    def explain(self) -> Explanation:
        """Explain the current rhythm state."""
        state = self.get_state()
        event_ids = [str(event_id) for event_id in state.applied_event_ids]
        return Explanation(
            summary=state.last_transition_reason,
            referenced_event_ids=event_ids,
            referenced_state_fields=[
                "current_rhythm",
                "current_category",
                "last_checked_at_event_id",
                "confidence",
            ],
            metadata={
                "current_rhythm": state.current_rhythm,
                "current_category": state.current_category,
            },
        )

    def replay(self, events: Iterable[ClinicalEvent]) -> RhythmState:
        """Replay events deterministically, respecting correction supersession."""
        ordered_events = tuple(events)
        superseded_event_ids = {
            event.supersedes_event_id
            for event in ordered_events
            if event.supersedes_event_id is not None
        }

        self.reset()
        for event in ordered_events:
            if event.id in superseded_event_ids:
                continue
            self.apply_event(event)
        return self.get_state()

    def _transition(self, event: ClinicalEvent, rhythm: RhythmName) -> RhythmState:
        category = self._category_for(rhythm)
        reason = self._transition_reason(rhythm, category, event)
        self._state = RhythmState(
            current_rhythm=rhythm,
            current_category=category,
            last_checked_at_event_id=event.id,
            last_checked_at=event.timestamp,
            confidence=event.confidence,
            applied_event_ids=(*self.get_state().applied_event_ids, event.id),
            last_transition_reason=reason,
        )
        return self.get_state()

    @staticmethod
    def _rhythm_from_payload(payload: Mapping[str, Any]) -> RhythmName:
        raw_value = (
            payload.get("rhythm")
            or payload.get("rhythm_name")
            or payload.get("detected_rhythm")
        )

        if isinstance(raw_value, RhythmName):
            return raw_value

        if raw_value is None:
            if payload.get("shockable") is True:
                return RhythmName.SHOCKABLE_UNKNOWN
            if payload.get("shockable") is False:
                return RhythmName.NON_SHOCKABLE_UNKNOWN
            return RhythmName.UNKNOWN

        normalized = str(raw_value).strip().lower().replace("-", "_")
        return _RHYTHM_ALIASES.get(normalized, RhythmName.UNKNOWN)

    @staticmethod
    def _category_for(rhythm: RhythmName) -> RhythmCategory:
        if rhythm in {
            RhythmName.VF,
            RhythmName.PULSELESS_VT,
            RhythmName.SHOCKABLE_UNKNOWN,
        }:
            return RhythmCategory.SHOCKABLE
        if rhythm in {
            RhythmName.PEA,
            RhythmName.ASYSTOLE,
            RhythmName.NON_SHOCKABLE_UNKNOWN,
        }:
            return RhythmCategory.NON_SHOCKABLE
        if rhythm == RhythmName.ROSC:
            return RhythmCategory.ROSC
        if rhythm == RhythmName.ORGANIZED:
            return RhythmCategory.ORGANIZED
        return RhythmCategory.UNKNOWN

    @staticmethod
    def _transition_reason(
        rhythm: RhythmName, category: RhythmCategory, event: ClinicalEvent
    ) -> str:
        if category == RhythmCategory.SHOCKABLE:
            return f"{rhythm.value} detected from event {event.id}; rhythm pathway is shockable."
        if category == RhythmCategory.NON_SHOCKABLE:
            return f"{rhythm.value} detected from event {event.id}; rhythm pathway is nonshockable."
        if category == RhythmCategory.ROSC:
            return f"ROSC detected from event {event.id}; transition to post-cardiac arrest care."
        if category == RhythmCategory.ORGANIZED:
            return f"Organized rhythm detected from event {event.id}; assess for ROSC."
        return f"Unknown rhythm from event {event.id}; rhythm requires confirmation."
