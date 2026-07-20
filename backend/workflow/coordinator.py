from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.workflow.recommendations import Recommendation, RecommendationPriority


class ActionKind(StrEnum):
    ASSESS_RHYTHM = "assess_rhythm"
    DELIVER_SHOCK = "deliver_shock"
    RESUME_CPR = "resume_cpr"
    CONTINUE_CPR = "continue_cpr"
    GIVE_EPINEPHRINE = "give_epinephrine"
    CONSIDER_AMIODARONE = "consider_amiodarone"
    CONSIDER_LIDOCAINE = "consider_lidocaine"
    CONSIDER_REVERSIBLE_CAUSE = "consider_reversible_cause"
    TRANSITION_TO_POST_ARREST_CARE = "transition_to_post_arrest_care"
    CONFIRM_RHYTHM = "confirm_rhythm"


class WorkflowPhase(StrEnum):
    UNKNOWN = "unknown"
    ARREST_RECOGNIZED = "arrest_recognized"
    AWAITING_RHYTHM_ASSESSMENT = "awaiting_rhythm_assessment"
    SHOCKABLE_ARREST = "shockable_arrest"
    POST_SHOCK_CPR = "post_shock_cpr"
    NONSHOCKABLE_ARREST = "nonshockable_arrest"
    ROSC = "rosc"
    POST_CARDIAC_ARREST_CARE = "post_cardiac_arrest_care"
    CONFLICT_OR_CONFIRMATION_NEEDED = "conflict_or_confirmation_needed"


class OwnedRecommendation(BaseModel):
    model_config = ConfigDict(frozen=True)

    owner_machine: str
    action_kind: ActionKind
    recommendation: Recommendation


class SuppressedRecommendation(BaseModel):
    model_config = ConfigDict(frozen=True)

    owner_machine: str
    action_kind: ActionKind
    recommendation_id: str
    reason: str


class WorkflowCoordinatorInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    machine_states: Mapping[str, Any] = Field(default_factory=dict)
    machine_recommendations: tuple[OwnedRecommendation, ...] = Field(default_factory=tuple)
    active_confirmation_requests: tuple[str, ...] = Field(default_factory=tuple)
    accepted_event_timeline: tuple[Any, ...] = Field(default_factory=tuple)
    replay_metadata: Mapping[str, Any] = Field(default_factory=dict)
    safety_flags: tuple[str, ...] = Field(default_factory=tuple)


class VisibleStateSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    rhythm: str = "unknown"
    pathway: str = "unknown"
    cpr_status: str = "unknown"
    cpr_cycle_number: int = 0
    cpr_cycle_elapsed_seconds: int | None = None
    shock_count: int = 0
    medication_summary: str = "No medications recorded."
    rosc_status: str = "unknown"
    top_reversible_causes: tuple[str, ...] = Field(default_factory=tuple)


class WorkflowPresentationDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    phase: WorkflowPhase
    primary_action: OwnedRecommendation | None = None
    secondary_actions: tuple[OwnedRecommendation, ...] = Field(default_factory=tuple)
    suppressed_actions: tuple[SuppressedRecommendation, ...] = Field(default_factory=tuple)
    rationale: str
    visible_state_summary: VisibleStateSummary
    safety_flags: tuple[str, ...] = Field(default_factory=tuple)
    source_recommendation_ids: tuple[str, ...] = Field(default_factory=tuple)
    source_state_fields: tuple[str, ...] = Field(default_factory=tuple)


_DOCUMENTED_OWNER: Mapping[ActionKind, str] = {
    ActionKind.ASSESS_RHYTHM: "cpr",
    ActionKind.DELIVER_SHOCK: "shocks",
    ActionKind.RESUME_CPR: "cpr",
    ActionKind.CONTINUE_CPR: "cpr",
    ActionKind.GIVE_EPINEPHRINE: "medications",
    ActionKind.CONSIDER_AMIODARONE: "medications",
    ActionKind.CONSIDER_LIDOCAINE: "medications",
    ActionKind.CONSIDER_REVERSIBLE_CAUSE: "reversible_causes",
    ActionKind.TRANSITION_TO_POST_ARREST_CARE: "rosc",
    ActionKind.CONFIRM_RHYTHM: "rhythm",
}

_PRIORITY_RANK: Mapping[RecommendationPriority, int] = {
    RecommendationPriority.CRITICAL: 0,
    RecommendationPriority.HIGH: 1,
    RecommendationPriority.MEDIUM: 2,
    RecommendationPriority.LOW: 3,
}

_OWNER_RANK: Mapping[str, int] = {
    "rosc": 0,
    "cpr": 1,
    "shocks": 2,
    "rhythm": 3,
    "medications": 4,
    "airway": 5,
    "reversible_causes": 6,
    "timers": 7,
}

_ACTIVE_ARREST_ACTIONS = {
    ActionKind.ASSESS_RHYTHM,
    ActionKind.DELIVER_SHOCK,
    ActionKind.RESUME_CPR,
    ActionKind.CONTINUE_CPR,
    ActionKind.GIVE_EPINEPHRINE,
    ActionKind.CONSIDER_AMIODARONE,
    ActionKind.CONSIDER_LIDOCAINE,
    ActionKind.CONSIDER_REVERSIBLE_CAUSE,
}


class WorkflowCoordinator:
    """Pure deterministic reducer over machine snapshots and recommendations."""

    def decide(self, coordinator_input: WorkflowCoordinatorInput) -> WorkflowPresentationDecision:
        states = coordinator_input.machine_states
        safety_flags = self._safety_flags(coordinator_input)
        phase = self._determine_phase(coordinator_input, safety_flags)
        visible_state = self._visible_state_summary(states)
        active, suppressed = self._suppress(
            recommendations=coordinator_input.machine_recommendations,
            phase=phase,
            states=states,
        )
        ordered = self._sort_recommendations(active, phase)
        primary = ordered[0] if ordered else self._fallback_action(phase)
        secondary = tuple(item for item in ordered[1:] if item != primary)[:3]
        source_ids = self._source_ids(primary, secondary)
        source_fields = self._source_fields(primary, secondary)

        return WorkflowPresentationDecision(
            phase=phase,
            primary_action=primary,
            secondary_actions=secondary,
            suppressed_actions=tuple(suppressed),
            rationale=self._rationale(phase, primary, suppressed, safety_flags),
            visible_state_summary=visible_state,
            safety_flags=tuple(safety_flags),
            source_recommendation_ids=source_ids,
            source_state_fields=source_fields,
        )

    def _determine_phase(
        self,
        coordinator_input: WorkflowCoordinatorInput,
        safety_flags: Sequence[str],
    ) -> WorkflowPhase:
        states = coordinator_input.machine_states
        if coordinator_input.active_confirmation_requests:
            return WorkflowPhase.CONFLICT_OR_CONFIRMATION_NEEDED
        if "replay_incomplete" in safety_flags:
            return WorkflowPhase.CONFLICT_OR_CONFIRMATION_NEEDED

        rhythm_category = self._value(self._state(states, "rhythm"), "current_category")
        cpr_status = self._value(self._state(states, "cpr"), "status")
        shock_due = self._value(self._state(states, "shocks"), "shock_due", False)
        shock_delivered_for_cycle = self._value(
            self._state(states, "shocks"),
            "shock_delivered_for_current_rhythm_check",
            False,
        )
        rosc_current = self._current_rosc_active(states)
        recurrent_arrest = self._recurrent_arrest_after_rosc(states)

        if rosc_current and not recurrent_arrest:
            if self._has_action(
                coordinator_input.machine_recommendations,
                ActionKind.TRANSITION_TO_POST_ARREST_CARE,
            ):
                return WorkflowPhase.POST_CARDIAC_ARREST_CARE
            return WorkflowPhase.ROSC

        if rhythm_category == "shockable" and shock_due:
            return WorkflowPhase.SHOCKABLE_ARREST
        if shock_delivered_for_cycle and cpr_status != "active":
            return WorkflowPhase.POST_SHOCK_CPR
        if rhythm_category == "non_shockable":
            return WorkflowPhase.NONSHOCKABLE_ARREST
        if cpr_status == "active" and rhythm_category in {None, "unknown"}:
            return WorkflowPhase.AWAITING_RHYTHM_ASSESSMENT
        if cpr_status in {"active", "paused"}:
            return WorkflowPhase.ARREST_RECOGNIZED
        return WorkflowPhase.UNKNOWN

    def _suppress(
        self,
        recommendations: Sequence[OwnedRecommendation],
        phase: WorkflowPhase,
        states: Mapping[str, Any],
    ) -> tuple[list[OwnedRecommendation], list[SuppressedRecommendation]]:
        kept: list[OwnedRecommendation] = []
        suppressed: list[SuppressedRecommendation] = []
        documented_owner_actions = {
            item.action_kind
            for item in recommendations
            if item.owner_machine == _DOCUMENTED_OWNER.get(item.action_kind)
        }

        rhythm_category = self._value(self._state(states, "rhythm"), "current_category")
        cpr_status = self._value(self._state(states, "cpr"), "status")
        rosc_current = self._current_rosc_active(states)
        recurrent_arrest = self._recurrent_arrest_after_rosc(states)
        shock_delivered_for_cycle = self._value(
            self._state(states, "shocks"),
            "shock_delivered_for_current_rhythm_check",
            False,
        )

        for item in recommendations:
            reason = self._suppression_reason(
                item=item,
                phase=phase,
                documented_owner_actions=documented_owner_actions,
                rhythm_category=rhythm_category,
                cpr_status=cpr_status,
                rosc_current=rosc_current,
                recurrent_arrest=recurrent_arrest,
                shock_delivered_for_cycle=shock_delivered_for_cycle,
            )
            if reason is None:
                kept.append(item)
            else:
                suppressed.append(
                    SuppressedRecommendation(
                        owner_machine=item.owner_machine,
                        action_kind=item.action_kind,
                        recommendation_id=item.recommendation.id,
                        reason=reason,
                    )
                )

        return kept, suppressed

    def _suppression_reason(
        self,
        item: OwnedRecommendation,
        phase: WorkflowPhase,
        documented_owner_actions: set[ActionKind],
        rhythm_category: str | None,
        cpr_status: str | None,
        rosc_current: bool,
        recurrent_arrest: bool,
        shock_delivered_for_cycle: bool,
    ) -> str | None:
        documented_owner = _DOCUMENTED_OWNER.get(item.action_kind)
        if (
            item.action_kind in documented_owner_actions
            and documented_owner is not None
            and item.owner_machine != documented_owner
        ):
            if item.action_kind == ActionKind.DELIVER_SHOCK and item.owner_machine == "rhythm":
                return "superseded_by_owner_machine: shocks"
            return "duplicate_action_wrong_owner"

        if rosc_current and not recurrent_arrest and item.action_kind in _ACTIVE_ARREST_ACTIONS:
            return "rosc_suppresses_active_arrest_action"

        if recurrent_arrest and item.action_kind == ActionKind.TRANSITION_TO_POST_ARREST_CARE:
            return "recurrent_arrest_suppresses_post_arrest_transition"

        if rhythm_category in {None, "unknown"} and item.action_kind in {
            ActionKind.DELIVER_SHOCK,
            ActionKind.CONSIDER_AMIODARONE,
            ActionKind.CONSIDER_LIDOCAINE,
        }:
            return "rhythm_unknown_suppresses_action"

        if rhythm_category == "non_shockable" and item.action_kind in {
            ActionKind.DELIVER_SHOCK,
            ActionKind.CONSIDER_AMIODARONE,
            ActionKind.CONSIDER_LIDOCAINE,
        }:
            return "nonshockable_rhythm_suppresses_action"

        if shock_delivered_for_cycle and item.action_kind == ActionKind.DELIVER_SHOCK:
            return "shock_already_delivered_for_current_rhythm_check"

        if cpr_status == "active" and item.action_kind == ActionKind.RESUME_CPR:
            return "cpr_already_active"

        if phase == WorkflowPhase.POST_SHOCK_CPR and item.action_kind == ActionKind.CONTINUE_CPR:
            return "resume_cpr_has_priority_after_shock"

        return None

    def _sort_recommendations(
        self,
        recommendations: Sequence[OwnedRecommendation],
        phase: WorkflowPhase,
    ) -> tuple[OwnedRecommendation, ...]:
        return tuple(
            sorted(
                recommendations,
                key=lambda item: (
                    self._phase_rank(phase, item.action_kind),
                    _PRIORITY_RANK[item.recommendation.priority],
                    _OWNER_RANK.get(item.owner_machine, 99),
                    item.recommendation.id,
                ),
            )
        )

    @staticmethod
    def _phase_rank(phase: WorkflowPhase, action_kind: ActionKind) -> int:
        phase_primary: Mapping[WorkflowPhase, tuple[ActionKind, ...]] = {
            WorkflowPhase.POST_CARDIAC_ARREST_CARE: (
                ActionKind.TRANSITION_TO_POST_ARREST_CARE,
            ),
            WorkflowPhase.ROSC: (ActionKind.TRANSITION_TO_POST_ARREST_CARE,),
            WorkflowPhase.SHOCKABLE_ARREST: (ActionKind.DELIVER_SHOCK,),
            WorkflowPhase.POST_SHOCK_CPR: (ActionKind.RESUME_CPR,),
            WorkflowPhase.AWAITING_RHYTHM_ASSESSMENT: (ActionKind.ASSESS_RHYTHM,),
            WorkflowPhase.NONSHOCKABLE_ARREST: (
                ActionKind.GIVE_EPINEPHRINE,
                ActionKind.CONTINUE_CPR,
                ActionKind.CONSIDER_REVERSIBLE_CAUSE,
            ),
            WorkflowPhase.CONFLICT_OR_CONFIRMATION_NEEDED: (ActionKind.CONFIRM_RHYTHM,),
        }
        preferred = phase_primary.get(phase, ())
        if action_kind in preferred:
            return preferred.index(action_kind)
        return len(preferred) + 1

    def _fallback_action(self, phase: WorkflowPhase) -> OwnedRecommendation | None:
        if phase == WorkflowPhase.UNKNOWN:
            return None
        action_kind = (
            ActionKind.CONFIRM_RHYTHM
            if phase == WorkflowPhase.CONFLICT_OR_CONFIRMATION_NEEDED
            else ActionKind.ASSESS_RHYTHM
        )
        return OwnedRecommendation(
            owner_machine="coordinator",
            action_kind=action_kind,
            recommendation=Recommendation(
                id=f"coordinator.{action_kind.value}",
                priority=RecommendationPriority.HIGH,
                message=(
                    "Confirm clinical state."
                    if action_kind == ActionKind.CONFIRM_RHYTHM
                    else "Assess rhythm."
                ),
                rationale="No machine-owned safe primary action is currently available.",
                referenced_state_fields=[],
                requires_confirmation=action_kind == ActionKind.CONFIRM_RHYTHM,
            ),
        )

    def _visible_state_summary(self, states: Mapping[str, Any]) -> VisibleStateSummary:
        cpr_state = self._state(states, "cpr")
        medication_state = self._state(states, "medications")
        shock_state = self._state(states, "shocks")
        rhythm_state = self._state(states, "rhythm")
        rosc_state = self._state(states, "rosc")
        reversible_cause_state = self._state(states, "reversible_causes")
        return VisibleStateSummary(
            rhythm=self._value(rhythm_state, "current_rhythm", "unknown") or "unknown",
            pathway=self._value(rhythm_state, "current_category", "unknown") or "unknown",
            cpr_status=self._value(cpr_state, "status", "unknown") or "unknown",
            cpr_cycle_number=self._value(cpr_state, "cycle_number", 0) or 0,
            shock_count=self._value(shock_state, "shock_count", 0) or 0,
            medication_summary=self._medication_summary(medication_state),
            rosc_status=self._value(rosc_state, "status", "unknown") or "unknown",
            top_reversible_causes=self._top_reversible_causes(reversible_cause_state),
        )

    def _safety_flags(self, coordinator_input: WorkflowCoordinatorInput) -> list[str]:
        flags: list[str] = list(coordinator_input.safety_flags)
        required = {"rhythm", "cpr", "shocks", "medications", "rosc"}
        missing = sorted(required.difference(coordinator_input.machine_states.keys()))
        if missing:
            flags.append(f"missing_machine_state: {','.join(missing)}")
        if coordinator_input.active_confirmation_requests:
            flags.append("confirmation_required")
        if coordinator_input.replay_metadata.get("machine_replay_failed"):
            flags.append("replay_incomplete")
        if self._recurrent_arrest_after_rosc(coordinator_input.machine_states):
            flags.append("recurrent_arrest_after_rosc_without_episode_segmentation")
        return flags

    def _rationale(
        self,
        phase: WorkflowPhase,
        primary: OwnedRecommendation | None,
        suppressed: Sequence[SuppressedRecommendation],
        safety_flags: Sequence[str],
    ) -> str:
        if primary is None:
            return "No accepted state is sufficient to select a safe primary action."
        parts = [primary.recommendation.rationale or primary.recommendation.message]
        parts.append(f"Workflow phase: {phase.value}.")
        if suppressed:
            parts.append(f"{len(suppressed)} recommendation(s) suppressed deterministically.")
        if safety_flags:
            parts.append(f"Safety flags: {', '.join(safety_flags)}.")
        return " ".join(parts)

    @staticmethod
    def _source_ids(
        primary: OwnedRecommendation | None,
        secondary: Sequence[OwnedRecommendation],
    ) -> tuple[str, ...]:
        items = (() if primary is None else (primary,)) + tuple(secondary)
        return tuple(item.recommendation.id for item in items)

    @staticmethod
    def _source_fields(
        primary: OwnedRecommendation | None,
        secondary: Sequence[OwnedRecommendation],
    ) -> tuple[str, ...]:
        fields: list[str] = []
        items = (() if primary is None else (primary,)) + tuple(secondary)
        for item in items:
            for field in item.recommendation.referenced_state_fields:
                if field not in fields:
                    fields.append(field)
        return tuple(fields)

    @staticmethod
    def _has_action(
        recommendations: Sequence[OwnedRecommendation],
        action_kind: ActionKind,
    ) -> bool:
        return any(item.action_kind == action_kind for item in recommendations)

    @staticmethod
    def _state(states: Mapping[str, Any], name: str) -> Any:
        return states.get(name)

    @staticmethod
    def _value(state: Any, field: str, default: Any = None) -> Any:
        if state is None:
            return default
        value = getattr(state, field, default)
        if hasattr(value, "value"):
            return value.value
        return value

    def _current_rosc_active(self, states: Mapping[str, Any]) -> bool:
        rosc_state = self._state(states, "rosc")
        rhythm_state = self._state(states, "rhythm")
        return (
            self._value(rosc_state, "status") == "achieved"
            or self._value(rhythm_state, "current_category") == "rosc"
        )

    def _recurrent_arrest_after_rosc(self, states: Mapping[str, Any]) -> bool:
        rosc_state = self._state(states, "rosc")
        rosc_at = self._value(rosc_state, "achieved_at")
        if rosc_at is None:
            return False

        rhythm_state = self._state(states, "rhythm")
        rhythm_at = self._value(rhythm_state, "last_checked_at")
        rhythm_category = self._value(rhythm_state, "current_category")
        if rhythm_at is not None and rhythm_at > rosc_at and rhythm_category in {
            "shockable",
            "non_shockable",
        }:
            return True

        cpr_state = self._state(states, "cpr")
        cpr_at = self._value(cpr_state, "last_cpr_event_at")
        cpr_status = self._value(cpr_state, "status")
        return cpr_at is not None and cpr_at > rosc_at and cpr_status == "active"

    @staticmethod
    def _medication_summary(state: Any) -> str:
        if state is None:
            return "Medication state unavailable."
        administrations = getattr(state, "administrations", ())
        if not administrations:
            return "No medications recorded."
        latest = administrations[-1]
        medication = getattr(latest, "medication_name", "unknown")
        dose = getattr(latest, "dose", None)
        unit = getattr(latest, "unit", None)
        if dose is not None and unit is not None:
            return f"Last medication: {medication} {dose:g} {unit}."
        return f"Last medication: {medication}."

    @staticmethod
    def _top_reversible_causes(state: Any) -> tuple[str, ...]:
        if state is None:
            return ()
        causes = getattr(state, "causes", ())
        ranked = sorted(
            causes,
            key=lambda cause: (
                -float(getattr(cause, "confidence", 0.0)),
                str(getattr(cause, "name", "")),
            ),
        )
        return tuple(str(getattr(cause, "name", "")) for cause in ranked[:3])
