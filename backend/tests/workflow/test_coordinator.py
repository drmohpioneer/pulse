from backend.tests.workflow.helpers import at, make_event
from backend.workflow.coordinator import (
    ActionKind,
    OwnedRecommendation,
    WorkflowCoordinator,
    WorkflowCoordinatorInput,
    WorkflowPhase,
)
from backend.workflow.cpr import CPRStateMachine
from backend.workflow.events import EventType
from backend.workflow.medications import MedicationStateMachine
from backend.workflow.rhythm import RhythmStateMachine
from backend.workflow.rosc import ROSCStateMachine
from backend.workflow.shocks import ShockStateMachine


def _wrap(owner: str, action_kind: ActionKind, recommendation):
    return OwnedRecommendation(
        owner_machine=owner,
        action_kind=action_kind,
        recommendation=recommendation,
    )


def _snapshot(
    *,
    rhythm: RhythmStateMachine | None = None,
    cpr: CPRStateMachine | None = None,
    shocks: ShockStateMachine | None = None,
    medications: MedicationStateMachine | None = None,
    rosc: ROSCStateMachine | None = None,
):
    return {
        key: machine.get_state()
        for key, machine in {
            "rhythm": rhythm,
            "cpr": cpr,
            "shocks": shocks,
            "medications": medications,
            "rosc": rosc,
        }.items()
        if machine is not None
    }


def test_cpr_started_with_unknown_rhythm_selects_assess_rhythm() -> None:
    cpr = CPRStateMachine()
    cpr.apply_event(make_event(EventType.CPR_STARTED))
    recommendations = [
        _wrap("cpr", ActionKind.ASSESS_RHYTHM, cpr.get_recommendations(as_of=at(minutes=2))[0])
    ]

    decision = WorkflowCoordinator().decide(
        WorkflowCoordinatorInput(
            machine_states=_snapshot(cpr=cpr),
            machine_recommendations=tuple(recommendations),
        )
    )

    assert decision.phase == WorkflowPhase.AWAITING_RHYTHM_ASSESSMENT
    assert decision.primary_action is not None
    assert decision.primary_action.action_kind == ActionKind.ASSESS_RHYTHM


def test_vf_keeps_shock_owner_and_suppresses_rhythm_duplicate() -> None:
    rhythm = RhythmStateMachine()
    shocks = ShockStateMachine()
    event = make_event(EventType.RHYTHM_CHECKED, rhythm="vf")
    rhythm.apply_event(event)
    shocks.apply_event(event)
    recommendations = [
        _wrap("rhythm", ActionKind.DELIVER_SHOCK, rhythm.get_recommendations()[0]),
        _wrap("shocks", ActionKind.DELIVER_SHOCK, shocks.get_recommendations()[0]),
    ]

    decision = WorkflowCoordinator().decide(
        WorkflowCoordinatorInput(
            machine_states=_snapshot(rhythm=rhythm, shocks=shocks),
            machine_recommendations=tuple(recommendations),
        )
    )

    assert decision.phase == WorkflowPhase.SHOCKABLE_ARREST
    assert decision.primary_action is not None
    assert decision.primary_action.owner_machine == "shocks"
    assert decision.primary_action.recommendation.message == "Deliver shock."
    assert decision.suppressed_actions[0].reason == "superseded_by_owner_machine: shocks"


def test_after_shock_primary_action_is_resume_cpr() -> None:
    rhythm = RhythmStateMachine()
    shocks = ShockStateMachine()
    cpr = CPRStateMachine()
    for machine in (rhythm, shocks):
        machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="vf"))
    cpr.apply_event(make_event(EventType.CPR_STARTED))
    shock_event = make_event(EventType.SHOCK_DELIVERED, minutes=1)
    shocks.apply_event(shock_event)
    cpr.apply_event(shock_event)
    recommendations = [
        _wrap("cpr", ActionKind.RESUME_CPR, cpr.get_recommendations(as_of=at(minutes=1))[0])
    ]

    decision = WorkflowCoordinator().decide(
        WorkflowCoordinatorInput(
            machine_states=_snapshot(rhythm=rhythm, cpr=cpr, shocks=shocks),
            machine_recommendations=tuple(recommendations),
        )
    )

    assert decision.phase == WorkflowPhase.POST_SHOCK_CPR
    assert decision.primary_action is not None
    assert decision.primary_action.action_kind == ActionKind.RESUME_CPR


def test_confirmed_rosc_suppresses_active_arrest_actions() -> None:
    rhythm = RhythmStateMachine()
    shocks = ShockStateMachine()
    rosc = ROSCStateMachine()
    rhythm_event = make_event(EventType.RHYTHM_CHECKED, rhythm="vf")
    for machine in (rhythm, shocks):
        machine.apply_event(rhythm_event)
    rosc.apply_event(make_event(EventType.ROSC_ACHIEVED, minutes=1))
    recommendations = [
        _wrap("shocks", ActionKind.DELIVER_SHOCK, shocks.get_recommendations()[0]),
        _wrap("rosc", ActionKind.TRANSITION_TO_POST_ARREST_CARE, rosc.get_recommendations()[0]),
    ]

    decision = WorkflowCoordinator().decide(
        WorkflowCoordinatorInput(
            machine_states=_snapshot(rhythm=rhythm, shocks=shocks, rosc=rosc),
            machine_recommendations=tuple(recommendations),
        )
    )

    assert decision.phase == WorkflowPhase.POST_CARDIAC_ARREST_CARE
    assert decision.primary_action is not None
    assert decision.primary_action.action_kind == ActionKind.TRANSITION_TO_POST_ARREST_CARE
    assert any(item.action_kind == ActionKind.DELIVER_SHOCK for item in decision.suppressed_actions)


def test_recurrent_arrest_after_rosc_allows_active_action_without_resetting_history() -> None:
    rhythm = RhythmStateMachine()
    shocks = ShockStateMachine()
    rosc = ROSCStateMachine()
    first_vf = make_event(EventType.RHYTHM_CHECKED, rhythm="vf")
    for machine in (rhythm, shocks):
        machine.apply_event(first_vf)
        machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=1))
    rosc.apply_event(make_event(EventType.ROSC_ACHIEVED, minutes=2))
    recurrent_vf = make_event(EventType.RHYTHM_CHECKED, rhythm="vf", minutes=5)
    for machine in (rhythm, shocks):
        machine.apply_event(recurrent_vf)
    recommendations = [
        _wrap("shocks", ActionKind.DELIVER_SHOCK, shocks.get_recommendations()[0]),
        _wrap("rosc", ActionKind.TRANSITION_TO_POST_ARREST_CARE, rosc.get_recommendations()[0]),
    ]

    decision = WorkflowCoordinator().decide(
        WorkflowCoordinatorInput(
            machine_states=_snapshot(rhythm=rhythm, shocks=shocks, rosc=rosc),
            machine_recommendations=tuple(recommendations),
        )
    )

    assert decision.primary_action is not None
    assert decision.primary_action.action_kind == ActionKind.DELIVER_SHOCK
    assert "recurrent_arrest_after_rosc_without_episode_segmentation" in decision.safety_flags
    assert shocks.get_state().shock_count == 1


def test_missing_machine_state_degrades_with_safety_flag() -> None:
    decision = WorkflowCoordinator().decide(WorkflowCoordinatorInput())

    assert decision.phase == WorkflowPhase.UNKNOWN
    assert decision.primary_action is None
    assert any(flag.startswith("missing_machine_state:") for flag in decision.safety_flags)


def test_replayed_snapshots_produce_identical_coordinator_output() -> None:
    events = (
        make_event(EventType.CPR_STARTED),
        make_event(EventType.RHYTHM_CHECKED, rhythm="vf", minutes=2),
    )
    rhythm = RhythmStateMachine()
    cpr = CPRStateMachine()
    shocks = ShockStateMachine()
    rhythm.replay(events)
    cpr.replay(events)
    shocks.replay(events)
    recommendations = (
        _wrap("rhythm", ActionKind.DELIVER_SHOCK, rhythm.get_recommendations()[0]),
        _wrap("shocks", ActionKind.DELIVER_SHOCK, shocks.get_recommendations()[0]),
    )
    coordinator_input = WorkflowCoordinatorInput(
        machine_states=_snapshot(rhythm=rhythm, cpr=cpr, shocks=shocks),
        machine_recommendations=recommendations,
    )

    first = WorkflowCoordinator().decide(coordinator_input)
    second = WorkflowCoordinator().decide(coordinator_input)

    assert first == second
