from datetime import datetime

from pydantic import BaseModel, ConfigDict

from backend.models.common import Explanation
from backend.workflow.base import BaseWorkflowStateMachine
from backend.workflow.events import ClinicalEvent
from backend.workflow.recommendations import Recommendation


class TimerState(BaseModel):
    model_config = ConfigDict(frozen=True)

    arrest_started_at: datetime | None = None
    cpr_cycle_started_at: datetime | None = None
    last_rhythm_check_at: datetime | None = None
    last_medication_at: datetime | None = None


class TimerStateMachine(BaseWorkflowStateMachine[TimerState]):
    """Deterministic timer state machine.

    Framework behavior (reset/replay/get_state) is inherited. No AI, UI, IO, or
    ACLS logic lives here yet.
    """

    def _initial_state(self) -> TimerState:
        return TimerState()

    def apply_event(self, event: ClinicalEvent) -> TimerState:
        """Apply an accepted event to timer state."""
        # TODO: Define timer recalculation from event history (arrest clock,
        #       CPR cycle, rhythm-check interval). Deterministic and IO-free.
        raise NotImplementedError

    def get_recommendations(self) -> list[Recommendation]:
        """Return deterministic next actions from timer state."""
        # TODO: Derive timing reminders (e.g. rhythm-check due) after review.
        raise NotImplementedError

    def explain(self) -> Explanation:
        """Explain the current timer state."""
        # TODO: Build explanation from event history + timer state references.
        raise NotImplementedError
