from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.workflow.coordinator import WorkflowPresentationDecision
from backend.workflow.events import ClinicalEvent, EventStatus


_ACCEPTED_STATUSES = {EventStatus.ACCEPTED, EventStatus.CORRECTED}


class FrozenCopilotModel(BaseModel):
    model_config = ConfigDict(frozen=True, validate_default=True)


class CopilotRequest(FrozenCopilotModel):
    """Read-only input contract for the Phase 3 clinical copilot.

    The copilot receives already-computed deterministic state. It never receives
    an engine, state machine instance, event processor, or raw audio handle.
    """

    coordinator_decision: WorkflowPresentationDecision
    timeline: tuple[ClinicalEvent, ...] = Field(default_factory=tuple)
    accepted_events: tuple[ClinicalEvent, ...] = Field(default_factory=tuple)
    machine_states: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("timeline", "accepted_events")
    @classmethod
    def validate_events_are_accepted(
        cls, value: tuple[ClinicalEvent, ...]
    ) -> tuple[ClinicalEvent, ...]:
        for event in value:
            if event.status not in _ACCEPTED_STATUSES:
                raise ValueError("copilot input may only include accepted/corrected events")
        return value

    @field_validator("machine_states", mode="after")
    @classmethod
    def freeze_machine_states(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return MappingProxyType(dict(value))


class CopilotResponse(FrozenCopilotModel):
    message: str
    priority: Literal["low", "medium", "high"]
    reason: str
    referenced_state_fields: tuple[str, ...] = Field(default_factory=tuple)
    referenced_event_ids: tuple[str, ...] = Field(default_factory=tuple)
    source_recommendation_ids: tuple[str, ...] = Field(default_factory=tuple)
    requires_confirmation: bool = False
    safety_constraints: tuple[str, ...] = Field(
        default=(
            "deterministic_engine_is_source_of_clinical_decisions",
            "copilot_does_not_create_or_modify_events",
            "copilot_does_not_recommend_treatments_independently",
        )
    )


class ClinicalCopilot(Protocol):
    def generate(self, request: CopilotRequest) -> CopilotResponse:
        """Generate advisory output from structured state only."""
        # TODO: Add LLM adapter after prompt, safety, and provider decisions are accepted.
        ...


class StateBoundClinicalCopilot:
    """Phase 3 safe copilot adapter.

    This class is intentionally deterministic for now. A future LLM adapter may
    replace the text generation step, but it must preserve this input/output
    boundary and the same non-mutation guarantees.
    """

    def generate(self, request: CopilotRequest) -> CopilotResponse:
        primary_action = request.coordinator_decision.primary_action
        action_text = (
            primary_action.recommendation.message
            if primary_action is not None
            else "No active deterministic action."
        )
        priority = _priority_from_phase(str(request.coordinator_decision.phase))
        referenced_event_ids = tuple(str(event.id) for event in request.accepted_events[-5:])

        return CopilotResponse(
            message=f"Deterministic workflow focus: {action_text}",
            priority=priority,
            reason=request.coordinator_decision.rationale,
            referenced_state_fields=request.coordinator_decision.source_state_fields,
            referenced_event_ids=referenced_event_ids,
            source_recommendation_ids=request.coordinator_decision.source_recommendation_ids,
            requires_confirmation=bool(request.coordinator_decision.safety_flags),
        )


def _priority_from_phase(phase: str) -> Literal["low", "medium", "high"]:
    if phase in {"shockable_arrest", "non_shockable_arrest", "post_shock_cpr"}:
        return "high"
    if phase in {"awaiting_rhythm_assessment", "rosc"}:
        return "medium"
    return "low"
