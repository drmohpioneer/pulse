import random

import pytest
from pydantic import ValidationError

from backend.tests.workflow.helpers import at, make_event
from backend.workflow.coordinator import (
    ActionKind,
    OwnedRecommendation,
    WorkflowCoordinator,
    WorkflowCoordinatorInput,
)
from backend.workflow.cpr import CPRStateMachine
from backend.workflow.events import EventType
from backend.workflow.hs_ts import ReversibleCauseStateMachine
from backend.workflow.medications import MedicationStateMachine
from backend.workflow.recommendations import Recommendation
from backend.workflow.rhythm import RhythmStateMachine
from backend.workflow.rosc import ROSCStateMachine
from backend.workflow.shocks import ShockStateMachine


def _wrap(owner: str, action_kind: ActionKind, recommendation: Recommendation) -> OwnedRecommendation:
    return OwnedRecommendation(
        owner_machine=owner,
        action_kind=action_kind,
        recommendation=recommendation,
    )


def _states(
    rhythm: RhythmStateMachine,
    cpr: CPRStateMachine,
    shocks: ShockStateMachine,
    medications: MedicationStateMachine,
    rosc: ROSCStateMachine,
    reversible_causes: ReversibleCauseStateMachine,
):
    return {
        "rhythm": rhythm.get_state(),
        "cpr": cpr.get_state(),
        "shocks": shocks.get_state(),
        "medications": medications.get_state(),
        "rosc": rosc.get_state(),
        "reversible_causes": reversible_causes.get_state(),
    }


def _recommendations(
    rhythm: RhythmStateMachine,
    cpr: CPRStateMachine,
    shocks: ShockStateMachine,
    medications: MedicationStateMachine,
    rosc: ROSCStateMachine,
    reversible_causes: ReversibleCauseStateMachine,
):
    items: list[OwnedRecommendation] = []
    for recommendation in rhythm.get_recommendations():
        action = (
            ActionKind.DELIVER_SHOCK
            if recommendation.id == "rhythm.shockable.deliver_shock"
            else ActionKind.CONFIRM_RHYTHM
        )
        items.append(_wrap("rhythm", action, recommendation))
    for recommendation in cpr.get_recommendations(as_of=at(minutes=10)):
        action = {
            "cpr.assess_rhythm": ActionKind.ASSESS_RHYTHM,
            "cpr.resume_cpr": ActionKind.RESUME_CPR,
            "cpr.continue_cpr": ActionKind.CONTINUE_CPR,
        }[recommendation.id]
        items.append(_wrap("cpr", action, recommendation))
    for recommendation in shocks.get_recommendations():
        items.append(_wrap("shocks", ActionKind.DELIVER_SHOCK, recommendation))
    for recommendation in medications.get_recommendations(as_of=at(minutes=10)):
        action = {
            "medications.give_epinephrine": ActionKind.GIVE_EPINEPHRINE,
            "medications.consider_amiodarone": ActionKind.CONSIDER_AMIODARONE,
            "medications.consider_lidocaine": ActionKind.CONSIDER_LIDOCAINE,
        }[recommendation.id]
        items.append(_wrap("medications", action, recommendation))
    for recommendation in rosc.get_recommendations():
        items.append(_wrap("rosc", ActionKind.TRANSITION_TO_POST_ARREST_CARE, recommendation))
    for recommendation in reversible_causes.get_recommendations():
        items.append(
            _wrap(
                "reversible_causes",
                ActionKind.CONSIDER_REVERSIBLE_CAUSE,
                recommendation,
            )
        )
    return tuple(items)


def _replay_all(events):
    rhythm = RhythmStateMachine()
    cpr = CPRStateMachine()
    shocks = ShockStateMachine()
    medications = MedicationStateMachine()
    rosc = ROSCStateMachine()
    reversible_causes = ReversibleCauseStateMachine()
    for machine in (rhythm, cpr, shocks, medications, rosc, reversible_causes):
        machine.replay(events)
    return rhythm, cpr, shocks, medications, rosc, reversible_causes


def test_public_state_snapshots_cannot_mutate_machine_state() -> None:
    machine = ShockStateMachine()
    machine.apply_event(make_event(EventType.SHOCK_DELIVERED))

    leaked_state = machine.get_state()
    with pytest.raises(ValidationError):
        leaked_state.shock_count = -99

    assert machine.get_state().shock_count == 1


def test_epinephrine_is_due_at_four_minutes_not_three_minutes() -> None:
    machine = MedicationStateMachine()
    machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="pea"))
    machine.apply_event(make_event(EventType.MEDICATION_GIVEN, medication="epinephrine", dose=1))

    assert machine.get_recommendations(as_of=at(minutes=3, seconds=59)) == []
    assert machine.get_recommendations(as_of=at(minutes=4))[0].id == "medications.give_epinephrine"


def test_recurrent_arrest_after_rosc_suppresses_post_arrest_transition_action() -> None:
    events = (
        make_event(EventType.RHYTHM_CHECKED, rhythm="vf"),
        make_event(EventType.SHOCK_DELIVERED, minutes=1),
        make_event(EventType.ROSC_ACHIEVED, minutes=2),
        make_event(EventType.RHYTHM_CHECKED, rhythm="vf", minutes=3),
    )
    rhythm, cpr, shocks, medications, rosc, reversible_causes = _replay_all(events)

    decision = WorkflowCoordinator().decide(
        WorkflowCoordinatorInput(
            machine_states=_states(rhythm, cpr, shocks, medications, rosc, reversible_causes),
            machine_recommendations=_recommendations(
                rhythm, cpr, shocks, medications, rosc, reversible_causes
            ),
        )
    )

    visible_actions = [decision.primary_action, *decision.secondary_actions]
    assert all(
        action is None
        or action.action_kind != ActionKind.TRANSITION_TO_POST_ARREST_CARE
        for action in visible_actions
    )


def test_replay_same_chaotic_history_is_identical_for_100_iterations() -> None:
    events = (
        make_event(EventType.CPR_STARTED),
        make_event(EventType.RHYTHM_CHECKED, rhythm="pea", minutes=1),
        make_event(EventType.MEDICATION_GIVEN, medication="epinephrine", dose=1, minutes=2),
        make_event(EventType.RHYTHM_CHECKED, rhythm="vf", minutes=4),
        make_event(EventType.SHOCK_DELIVERED, minutes=5),
        make_event(EventType.CPR_RESUMED, minutes=5, seconds=5),
        make_event(EventType.RHYTHM_CHECKED, rhythm="vf", minutes=7),
        make_event(EventType.SHOCK_DELIVERED, minutes=8),
        make_event(EventType.MEDICATION_GIVEN, medication="amiodarone", dose=300, minutes=9),
        make_event(
            EventType.REVERSIBLE_CAUSE_CONSIDERED,
            minutes=9,
            cause="Hypoxia",
            cause_confidence=0.8,
            reversible_cause_evidence=["simulation"],
            suggested_intervention="optimize oxygenation",
        ),
        make_event(EventType.ROSC_ACHIEVED, minutes=10),
    )

    baseline = tuple(machine.get_state() for machine in _replay_all(events))
    for _ in range(100):
        assert tuple(machine.get_state() for machine in _replay_all(events)) == baseline


def test_seeded_fuzz_sequences_do_not_crash_or_create_negative_counts() -> None:
    rng = random.Random(20260718)
    event_factories = (
        lambda i: make_event(EventType.CPR_STARTED, minutes=i),
        lambda i: make_event(EventType.CPR_PAUSED, minutes=i),
        lambda i: make_event(EventType.CPR_RESUMED, minutes=i),
        lambda i: make_event(EventType.RHYTHM_CHECKED, rhythm=rng.choice(("vf", "pulseless_vt", "pea", "asystole")), minutes=i),
        lambda i: make_event(EventType.SHOCK_DELIVERED, minutes=i),
        lambda i: make_event(EventType.MEDICATION_GIVEN, medication=rng.choice(("epinephrine", "amiodarone", "lidocaine", "calcium")), dose=1, minutes=i),
        lambda i: make_event(EventType.ROSC_ACHIEVED, minutes=i),
        lambda i: make_event(
            EventType.REVERSIBLE_CAUSE_CONSIDERED,
            minutes=i,
            cause=rng.choice(("Hypoxia", "Tamponade", "Hyperkalemia")),
            cause_confidence=rng.random(),
            reversible_cause_evidence=[f"fuzz-{i}"],
            suggested_intervention=rng.choice(
                (
                    "optimize oxygenation",
                    "prepare pericardiocentesis",
                    "prepare hyperkalemia treatment",
                )
            ),
        ),
    )

    for sequence_index in range(100):
        events = tuple(rng.choice(event_factories)(i) for i in range(sequence_index % 30))
        rhythm, cpr, shocks, medications, rosc, reversible_causes = _replay_all(events)
        decision = WorkflowCoordinator().decide(
            WorkflowCoordinatorInput(
                machine_states=_states(rhythm, cpr, shocks, medications, rosc, reversible_causes),
                machine_recommendations=_recommendations(
                    rhythm, cpr, shocks, medications, rosc, reversible_causes
                ),
            )
        )

        assert shocks.get_state().shock_count >= 0
        assert medications.get_state().epinephrine_count >= 0
        assert medications.get_state().amiodarone_count >= 0
        assert medications.get_state().lidocaine_count >= 0
        for cause in reversible_causes.get_state().causes:
            assert 0.0 <= cause.confidence <= 1.0
            assert cause.evidence_ids
        assert len([decision.primary_action]) <= 1
