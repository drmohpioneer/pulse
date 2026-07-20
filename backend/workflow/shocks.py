from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from backend.models.common import Explanation
from backend.workflow.base import BaseWorkflowStateMachine
from backend.workflow.events import ClinicalEvent, EventStatus, EventType
from backend.workflow.recommendations import Recommendation, RecommendationPriority
from backend.workflow.rhythm import RhythmCategory, RhythmName


class ShockRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: UUID
    timestamp: datetime


class ShockState(BaseModel):
    model_config = ConfigDict(frozen=True)

    shock_count: int = 0
    shock_history: tuple[ShockRecord, ...] = Field(default_factory=tuple)
    last_shock_at: datetime | None = None
    last_shock_event_id: UUID | None = None
    latest_rhythm_check_event_id: UUID | None = None
    latest_rhythm_checked_at: datetime | None = None
    latest_rhythm_name: RhythmName = RhythmName.UNKNOWN
    latest_rhythm_category: RhythmCategory = RhythmCategory.UNKNOWN
    shock_delivered_for_current_rhythm_check: bool = False
    rosc_achieved: bool = False

    @property
    def shock_due(self) -> bool:
        return (
            self.latest_rhythm_category == RhythmCategory.SHOCKABLE
            and not self.shock_delivered_for_current_rhythm_check
            and not self.rosc_achieved
        )


_ACCEPTED_STATUSES = {EventStatus.ACCEPTED, EventStatus.CORRECTED}
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
    "asystole": RhythmName.ASYSTOLE,
    "shockable": RhythmName.SHOCKABLE_UNKNOWN,
    "non_shockable": RhythmName.NON_SHOCKABLE_UNKNOWN,
    "nonshockable": RhythmName.NON_SHOCKABLE_UNKNOWN,
    "organized": RhythmName.ORGANIZED,
    "rosc": RhythmName.ROSC,
}


class ShockStateMachine(BaseWorkflowStateMachine[ShockState]):
    """Deterministic shock state machine for VF/pVT arrest."""

    def _initial_state(self) -> ShockState:
        return ShockState()

    def apply_event(self, event: ClinicalEvent) -> ShockState:
        if event.status not in _ACCEPTED_STATUSES:
            return self.get_state()

        if event.event_type == EventType.RHYTHM_CHECKED:
            rhythm = self._rhythm_from_payload(event.payload)
            category = self._category_for(rhythm)
            self._state = self.get_state().model_copy(
                update={
                    "latest_rhythm_check_event_id": event.id,
                    "latest_rhythm_checked_at": event.timestamp,
                    "latest_rhythm_name": rhythm,
                    "latest_rhythm_category": category,
                    "shock_delivered_for_current_rhythm_check": False,
                    "rosc_achieved": category == RhythmCategory.ROSC,
                }
            )
            return self.get_state()

        if event.event_type == EventType.SHOCK_DELIVERED:
            record = ShockRecord(event_id=event.id, timestamp=event.timestamp)
            state = self.get_state()
            self._state = state.model_copy(
                update={
                    "shock_count": state.shock_count + 1,
                    "shock_history": (*state.shock_history, record),
                    "last_shock_at": event.timestamp,
                    "last_shock_event_id": event.id,
                    "shock_delivered_for_current_rhythm_check": (
                        state.latest_rhythm_check_event_id is not None
                    ),
                }
            )
            return self.get_state()

        if event.event_type == EventType.ROSC_ACHIEVED:
            self._state = self.get_state().model_copy(
                update={"rosc_achieved": True}
            )
            return self.get_state()

        return self.get_state()

    def get_recommendations(self) -> list[Recommendation]:
        state = self.get_state()
        if not state.shock_due:
            return []

        return [
            Recommendation(
                id="shocks.deliver_shock",
                priority=RecommendationPriority.CRITICAL,
                message="Deliver shock.",
                rationale="The latest accepted rhythm check is VF/pVT shockable arrest and no shock is recorded for this rhythm-check cycle.",
                referenced_state_fields=[
                    "latest_rhythm_name",
                    "latest_rhythm_category",
                    "shock_delivered_for_current_rhythm_check",
                ],
            )
        ]

    def explain(self) -> Explanation:
        state = self.get_state()
        return Explanation(
            summary=(
                f"{state.shock_count} accepted shock event(s); "
                f"shock due is {state.shock_due}."
            ),
            referenced_event_ids=[
                str(event_id)
                for event_id in (
                    state.latest_rhythm_check_event_id,
                    state.last_shock_event_id,
                )
                if event_id is not None
            ],
            referenced_state_fields=[
                "shock_count",
                "latest_rhythm_category",
                "shock_delivered_for_current_rhythm_check",
                "rosc_achieved",
            ],
            metadata=state.model_dump(mode="json"),
        )

    def replay(self, events: Iterable[ClinicalEvent]) -> ShockState:
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

    @staticmethod
    def _rhythm_from_payload(payload: Mapping[str, Any]) -> RhythmName:
        raw_value = payload.get("rhythm") or payload.get("rhythm_name")
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
