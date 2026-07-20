from datetime import datetime

from backend.tests.workflow.helpers import at, make_event
from backend.workflow.coordinator import (
    ActionKind,
    OwnedRecommendation,
    WorkflowCoordinator,
    WorkflowCoordinatorInput,
)
from backend.workflow.cpr import CPRStateMachine
from backend.workflow.engine import ClinicalWorkflowEngine
from backend.workflow.event_processor import MachineRegistry, RoutingTable
from backend.workflow.events import EventType
from backend.workflow.hs_ts import ReversibleCauseStateMachine
from backend.workflow.medications import MedicationStateMachine
from backend.workflow.rhythm import RhythmStateMachine
from backend.workflow.rosc import ROSCStateMachine
from backend.workflow.shocks import ShockStateMachine


def _build_engine():
    registry = MachineRegistry()
    routing = RoutingTable(
        {
            EventType.CPR_STARTED: ["cpr"],
            EventType.CPR_PAUSED: ["cpr"],
            EventType.CPR_RESUMED: ["cpr"],
            EventType.RHYTHM_CHECKED: ["rhythm", "shocks", "medications", "cpr"],
            EventType.SHOCK_DELIVERED: ["shocks", "cpr", "medications"],
            EventType.MEDICATION_GIVEN: ["medications"],
            EventType.ROSC_ACHIEVED: ["rhythm", "shocks", "medications", "cpr", "rosc"],
            EventType.REVERSIBLE_CAUSE_CONSIDERED: ["reversible_causes"],
        }
    )
    engine = ClinicalWorkflowEngine(registry=registry, routing_table=routing)
    machines = {
        "rhythm": RhythmStateMachine(),
        "cpr": CPRStateMachine(),
        "shocks": ShockStateMachine(),
        "medications": MedicationStateMachine(),
        "rosc": ROSCStateMachine(),
        "reversible_causes": ReversibleCauseStateMachine(),
    }
    for name, machine in machines.items():
        engine.register_machine(name, machine)
    return engine, machines


def _recommendations(machines, as_of: datetime):
    items: list[OwnedRecommendation] = []
    mapping = {
        "rhythm.shockable.deliver_shock": ("rhythm", ActionKind.DELIVER_SHOCK),
        "rhythm.unknown.assess_rhythm": ("rhythm", ActionKind.CONFIRM_RHYTHM),
        "rhythm.non_shockable.cpr": ("rhythm", ActionKind.CONTINUE_CPR),
        "rhythm.non_shockable.epinephrine_asap": ("rhythm", ActionKind.GIVE_EPINEPHRINE),
        "rhythm.rosc.post_cardiac_arrest_care": (
            "rhythm",
            ActionKind.TRANSITION_TO_POST_ARREST_CARE,
        ),
        "cpr.assess_rhythm": ("cpr", ActionKind.ASSESS_RHYTHM),
        "cpr.resume_cpr": ("cpr", ActionKind.RESUME_CPR),
        "cpr.continue_cpr": ("cpr", ActionKind.CONTINUE_CPR),
        "shocks.deliver_shock": ("shocks", ActionKind.DELIVER_SHOCK),
        "medications.give_epinephrine": ("medications", ActionKind.GIVE_EPINEPHRINE),
        "medications.consider_amiodarone": (
            "medications",
            ActionKind.CONSIDER_AMIODARONE,
        ),
        "medications.consider_lidocaine": (
            "medications",
            ActionKind.CONSIDER_LIDOCAINE,
        ),
        "rosc.transition_to_post_arrest_care": (
            "rosc",
            ActionKind.TRANSITION_TO_POST_ARREST_CARE,
        ),
    }
    for name, machine in machines.items():
        if name in {"cpr", "medications"}:
            recs = machine.get_recommendations(as_of=as_of)
        else:
            recs = machine.get_recommendations()
        for recommendation in recs:
            if recommendation.id.startswith("hs_ts.suggested_intervention."):
                owner, action = "reversible_causes", ActionKind.CONSIDER_REVERSIBLE_CAUSE
            else:
                owner, action = mapping[recommendation.id]
            items.append(
                OwnedRecommendation(
                    owner_machine=owner,
                    action_kind=action,
                    recommendation=recommendation,
                )
            )
    return tuple(items)


def _coordinator_decision(machines, as_of: datetime):
    return WorkflowCoordinator().decide(
        WorkflowCoordinatorInput(
            machine_states={name: machine.get_state() for name, machine in machines.items()},
            machine_recommendations=_recommendations(machines, as_of),
        )
    )


def test_lidocaine_recommendation_flows_through_real_engine_and_coordinator() -> None:
    engine, machines = _build_engine()
    events = (
        make_event(EventType.RHYTHM_CHECKED, rhythm="vf"),
        make_event(EventType.SHOCK_DELIVERED, minutes=1),
        make_event(EventType.RHYTHM_CHECKED, rhythm="vf", minutes=3),
        make_event(EventType.SHOCK_DELIVERED, minutes=4),
        make_event(EventType.RHYTHM_CHECKED, rhythm="vf", minutes=6),
        make_event(EventType.SHOCK_DELIVERED, minutes=7),
    )

    for event in events:
        result = engine.process(event)
        assert result.ok

    decision = _coordinator_decision(machines, at(minutes=7))
    visible_actions = [decision.primary_action, *decision.secondary_actions]

    assert any(
        action is not None and action.action_kind == ActionKind.CONSIDER_LIDOCAINE
        for action in visible_actions
    )
    assert machines["medications"].get_state().lidocaine_count == 0


def test_lidocaine_history_persists_across_rhythm_transitions() -> None:
    engine, machines = _build_engine()
    events = (
        make_event(EventType.RHYTHM_CHECKED, rhythm="pea"),
        make_event(EventType.MEDICATION_GIVEN, medication="epinephrine", dose=1, minutes=1),
        make_event(EventType.RHYTHM_CHECKED, rhythm="vf", minutes=2),
        make_event(EventType.SHOCK_DELIVERED, minutes=3),
        make_event(EventType.SHOCK_DELIVERED, minutes=4),
        make_event(EventType.SHOCK_DELIVERED, minutes=5),
        make_event(EventType.MEDICATION_GIVEN, medication="lidocaine", dose=100, unit="mg", minutes=6),
    )

    for event in events:
        engine.process(event)

    medication_state = machines["medications"].get_state()

    assert medication_state.epinephrine_count == 1
    assert medication_state.lidocaine_count == 1
    assert medication_state.shock_count == 3


def test_reversible_cause_event_flows_to_coordinator_top_causes() -> None:
    engine, machines = _build_engine()
    event = make_event(
        EventType.REVERSIBLE_CAUSE_CONSIDERED,
        minutes=1,
        cause="Hypoxia",
        cause_confidence=0.9,
        reversible_cause_evidence=["low-spo2", "cyanosis"],
        suggested_intervention="optimize oxygenation",
    )

    result = engine.process(event)
    decision = _coordinator_decision(machines, at(minutes=1))

    assert result.ok
    assert machines["reversible_causes"].get_state().possible_cause == "Hypoxia"
    assert decision.visible_state_summary.top_reversible_causes == ("Hypoxia",)
    assert any(
        action.action_kind == ActionKind.CONSIDER_REVERSIBLE_CAUSE
        for action in (decision.primary_action, *decision.secondary_actions)
        if action is not None
    )


def test_rosc_suppresses_lidocaine_and_reversible_cause_active_actions() -> None:
    engine, machines = _build_engine()
    reversible_event = make_event(
        EventType.REVERSIBLE_CAUSE_CONSIDERED,
        cause="Tamponade",
        cause_confidence=0.9,
        reversible_cause_evidence=["pocus-effusion"],
        suggested_intervention="prepare pericardiocentesis",
    )
    events = (
        make_event(EventType.RHYTHM_CHECKED, rhythm="vf"),
        make_event(EventType.SHOCK_DELIVERED, minutes=1),
        make_event(EventType.SHOCK_DELIVERED, minutes=2),
        make_event(EventType.SHOCK_DELIVERED, minutes=3),
        reversible_event,
        make_event(EventType.ROSC_ACHIEVED, minutes=4),
    )

    for event in events:
        engine.process(event)

    decision = _coordinator_decision(machines, at(minutes=4))
    visible_actions = [decision.primary_action, *decision.secondary_actions]

    assert all(
        action is None
        or action.action_kind
        not in {ActionKind.CONSIDER_LIDOCAINE, ActionKind.CONSIDER_REVERSIBLE_CAUSE}
        for action in visible_actions
    )
