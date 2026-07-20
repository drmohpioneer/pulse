from collections.abc import Iterable
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from backend.models.common import Explanation
from backend.workflow.base import BaseWorkflowStateMachine
from backend.workflow.events import ClinicalEvent, EventStatus, EventType
from backend.workflow.recommendations import Recommendation, RecommendationPriority


class CPRStatus(StrEnum):
    UNKNOWN = "unknown"
    ACTIVE = "active"
    PAUSED = "paused"


class CPRState(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: CPRStatus = CPRStatus.UNKNOWN
    cycle_started_at: datetime | None = None
    cycle_number: int = 0
    last_started_event_id: UUID | None = None
    last_resumed_event_id: UUID | None = None
    last_paused_event_id: UUID | None = None
    last_cpr_event_id: UUID | None = None
    last_cpr_event_at: datetime | None = None
    last_rhythm_assessment_event_id: UUID | None = None
    last_rhythm_assessment_at: datetime | None = None
    shock_pending_resume: bool = False
    rosc_achieved: bool = False

    def rhythm_assessment_due(self, as_of: datetime) -> bool:
        if self.status != CPRStatus.ACTIVE or self.cycle_started_at is None:
            return False
        return as_of >= self.cycle_started_at + timedelta(minutes=2)

    def cycle_elapsed_seconds(self, as_of: datetime) -> int | None:
        if self.status != CPRStatus.ACTIVE or self.cycle_started_at is None:
            return None
        return max(0, int((as_of - self.cycle_started_at).total_seconds()))

    def hands_off_elapsed_seconds(self, as_of: datetime) -> int | None:
        if self.status != CPRStatus.PAUSED or self.last_cpr_event_at is None:
            return None
        return max(0, int((as_of - self.last_cpr_event_at).total_seconds()))


_ACCEPTED_STATUSES = {EventStatus.ACCEPTED, EventStatus.CORRECTED}


class CPRStateMachine(BaseWorkflowStateMachine[CPRState]):
    """Deterministic CPR state machine for the shockable arrest capability."""

    def _initial_state(self) -> CPRState:
        return CPRState()

    def apply_event(self, event: ClinicalEvent) -> CPRState:
        if event.status not in _ACCEPTED_STATUSES:
            return self.get_state()

        if event.event_type == EventType.CPR_STARTED:
            self._state = CPRState(
                status=CPRStatus.ACTIVE,
                cycle_started_at=event.timestamp,
                cycle_number=self._next_cycle_number(),
                last_started_event_id=event.id,
                last_resumed_event_id=self.get_state().last_resumed_event_id,
                last_paused_event_id=self.get_state().last_paused_event_id,
                last_cpr_event_id=event.id,
                last_cpr_event_at=event.timestamp,
                shock_pending_resume=False,
                rosc_achieved=False,
            )
            return self.get_state()

        if event.event_type == EventType.CPR_RESUMED:
            self._state = self.get_state().model_copy(
                update={
                    "status": CPRStatus.ACTIVE,
                    "cycle_started_at": event.timestamp,
                    "cycle_number": self._next_cycle_number(),
                    "last_resumed_event_id": event.id,
                    "last_cpr_event_id": event.id,
                    "last_cpr_event_at": event.timestamp,
                    "shock_pending_resume": False,
                    "rosc_achieved": False,
                }
            )
            return self.get_state()

        if event.event_type == EventType.CPR_PAUSED:
            self._state = self.get_state().model_copy(
                update={
                    "status": CPRStatus.PAUSED,
                    "last_paused_event_id": event.id,
                    "last_cpr_event_id": event.id,
                    "last_cpr_event_at": event.timestamp,
                }
            )
            return self.get_state()

        if event.event_type == EventType.SHOCK_DELIVERED:
            self._state = self.get_state().model_copy(
                update={
                    "status": CPRStatus.PAUSED,
                    "last_cpr_event_id": event.id,
                    "last_cpr_event_at": event.timestamp,
                    "shock_pending_resume": True,
                }
            )
            return self.get_state()

        if event.event_type == EventType.RHYTHM_CHECKED:
            self._state = self.get_state().model_copy(
                update={
                    "last_rhythm_assessment_event_id": event.id,
                    "last_rhythm_assessment_at": event.timestamp,
                }
            )
            return self.get_state()

        if event.event_type == EventType.ROSC_ACHIEVED:
            self._state = self.get_state().model_copy(
                update={
                    "status": CPRStatus.PAUSED,
                    "last_cpr_event_id": event.id,
                    "last_cpr_event_at": event.timestamp,
                    "shock_pending_resume": False,
                    "rosc_achieved": True,
                }
            )
            return self.get_state()

        return self.get_state()

    def get_recommendations(self, as_of: datetime | None = None) -> list[Recommendation]:
        state = self.get_state()
        if state.rosc_achieved:
            return []

        if state.status == CPRStatus.PAUSED or state.shock_pending_resume:
            return [
                Recommendation(
                    id="cpr.resume_cpr",
                    priority=RecommendationPriority.CRITICAL,
                    message="Resume CPR.",
                    rationale="CPR is not currently active; after shock delivery, compressions should resume immediately.",
                    referenced_state_fields=["status", "shock_pending_resume"],
                )
            ]

        if state.status == CPRStatus.ACTIVE:
            if state.last_rhythm_assessment_event_id is None:
                return [
                    Recommendation(
                        id="cpr.assess_rhythm",
                        priority=RecommendationPriority.HIGH,
                        message="Assess rhythm.",
                        rationale="CPR is active and no accepted rhythm assessment has been recorded in this resuscitation episode.",
                        referenced_state_fields=["status", "last_rhythm_assessment_event_id"],
                    )
                ]
            if as_of is not None and state.rhythm_assessment_due(as_of):
                return [
                    Recommendation(
                        id="cpr.assess_rhythm",
                        priority=RecommendationPriority.HIGH,
                        message="Assess rhythm.",
                        rationale="The active CPR cycle has reached the deterministic 2-minute rhythm assessment point.",
                        referenced_state_fields=["cycle_started_at", "cycle_number"],
                    )
                ]
            return [
                Recommendation(
                    id="cpr.continue_cpr",
                    priority=RecommendationPriority.HIGH,
                    message="Continue CPR.",
                    rationale="CPR is active and the current 2-minute cycle has not reached the rhythm assessment point.",
                    referenced_state_fields=["status", "cycle_started_at", "cycle_number"],
                )
            ]

        return [
            Recommendation(
                id="cpr.assess_rhythm",
                priority=RecommendationPriority.HIGH,
                message="Assess rhythm.",
                rationale="CPR/arrest workflow has no active CPR cycle yet; rhythm assessment is needed before pathway actions.",
                referenced_state_fields=["status"],
            )
        ]

    def explain(self) -> Explanation:
        state = self.get_state()
        return Explanation(
            summary=f"CPR status is {state.status.value}; cycle number is {state.cycle_number}.",
            referenced_event_ids=[
                str(event_id)
                for event_id in (
                    state.last_started_event_id,
                    state.last_resumed_event_id,
                    state.last_paused_event_id,
                    state.last_cpr_event_id,
                )
                if event_id is not None
            ],
            referenced_state_fields=[
                "status",
                "cycle_started_at",
                "cycle_number",
                "shock_pending_resume",
                "rosc_achieved",
                "last_rhythm_assessment_event_id",
            ],
            metadata=state.model_dump(mode="json"),
        )

    def replay(self, events: Iterable[ClinicalEvent]) -> CPRState:
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

    def _next_cycle_number(self) -> int:
        return self.get_state().cycle_number + 1
