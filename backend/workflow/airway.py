from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from backend.models.common import Explanation
from backend.workflow.base import BaseWorkflowStateMachine
from backend.workflow.events import ClinicalEvent
from backend.workflow.recommendations import Recommendation


class AirwayStatus(StrEnum):
    UNKNOWN = "unknown"
    BASIC = "basic"
    ADVANCED = "advanced"
    TODO = "todo"


class AirwayState(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: AirwayStatus = AirwayStatus.UNKNOWN
    secured_at_event_id: str | None = None
    confidence: float | None = None


class AirwayStateMachine(BaseWorkflowStateMachine[AirwayState]):
    """Deterministic airway state machine.

    Framework behavior (reset/replay/get_state) is inherited. No AI, UI, IO, or
    ACLS logic lives here yet.
    """

    def _initial_state(self) -> AirwayState:
        return AirwayState()

    def apply_event(self, event: ClinicalEvent) -> AirwayState:
        """Apply an accepted event to airway state."""
        # TODO: Define airway state transitions (basic/advanced secured) after
        #       clinical architecture review. Deterministic and IO-free.
        raise NotImplementedError

    def get_recommendations(self) -> list[Recommendation]:
        """Return deterministic next actions from airway state."""
        # TODO: Derive airway-related recommendations after ACLS rules accepted.
        raise NotImplementedError

    def explain(self) -> Explanation:
        """Explain the current airway state."""
        # TODO: Build explanation from event history + airway state references.
        raise NotImplementedError
