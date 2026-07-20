from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from backend.models.common import Explanation
from backend.workflow.base import BaseWorkflowStateMachine
from backend.workflow.events import ClinicalEvent, EventStatus, EventType
from backend.workflow.recommendations import Recommendation, RecommendationPriority


class ROSCStatus(StrEnum):
    UNKNOWN = "unknown"
    ACHIEVED = "achieved"


class ROSCState(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: ROSCStatus = ROSCStatus.UNKNOWN
    achieved_at_event_id: UUID | None = None
    achieved_at: datetime | None = None
    confidence: float | None = None


_ACCEPTED_STATUSES = {EventStatus.ACCEPTED, EventStatus.CORRECTED}


class ROSCStateMachine(BaseWorkflowStateMachine[ROSCState]):
    """Deterministic ROSC state machine for accepted ROSC events."""

    def _initial_state(self) -> ROSCState:
        return ROSCState()

    def apply_event(self, event: ClinicalEvent) -> ROSCState:
        if event.status not in _ACCEPTED_STATUSES:
            return self.get_state()

        if event.event_type != EventType.ROSC_ACHIEVED:
            return self.get_state()

        self._state = ROSCState(
            status=ROSCStatus.ACHIEVED,
            achieved_at_event_id=event.id,
            achieved_at=event.timestamp,
            confidence=event.confidence,
        )
        return self.get_state()

    def get_recommendations(self) -> list[Recommendation]:
        state = self.get_state()
        if state.status != ROSCStatus.ACHIEVED:
            return []

        return [
            Recommendation(
                id="rosc.transition_to_post_arrest_care",
                priority=RecommendationPriority.CRITICAL,
                message="Transition to post-cardiac arrest care.",
                rationale="An accepted ROSC event has been recorded.",
                referenced_state_fields=["status", "achieved_at_event_id", "confidence"],
            )
        ]

    def explain(self) -> Explanation:
        state = self.get_state()
        return Explanation(
            summary=f"ROSC status is {state.status.value}.",
            referenced_event_ids=(
                [str(state.achieved_at_event_id)]
                if state.achieved_at_event_id is not None
                else []
            ),
            referenced_state_fields=["status", "achieved_at_event_id", "confidence"],
            metadata=state.model_dump(mode="json"),
        )

    def replay(self, events: Iterable[ClinicalEvent]) -> ROSCState:
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
