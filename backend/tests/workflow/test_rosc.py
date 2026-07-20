from backend.tests.workflow.helpers import make_event
from backend.workflow.events import EventStatus, EventType
from backend.workflow.rosc import ROSCStateMachine, ROSCStatus


def test_accepted_rosc_event_sets_rosc_state() -> None:
    machine = ROSCStateMachine()
    event = make_event(EventType.ROSC_ACHIEVED)

    state = machine.apply_event(event)

    assert state.status == ROSCStatus.ACHIEVED
    assert state.achieved_at_event_id == event.id
    assert state.confidence == event.confidence


def test_rosc_recommendation_transitions_to_post_arrest_care() -> None:
    machine = ROSCStateMachine()
    machine.apply_event(make_event(EventType.ROSC_ACHIEVED))

    recommendations = machine.get_recommendations()

    assert recommendations[0].id == "rosc.transition_to_post_arrest_care"


def test_rosc_ignores_unrelated_and_unaccepted_events() -> None:
    machine = ROSCStateMachine()

    machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="vf"))
    machine.apply_event(make_event(EventType.ROSC_ACHIEVED, status=EventStatus.CANDIDATE))

    assert machine.get_state().status == ROSCStatus.UNKNOWN


def test_rosc_replay_ignores_superseded_rosc() -> None:
    machine = ROSCStateMachine()
    original = make_event(EventType.ROSC_ACHIEVED)
    correction = make_event(
        EventType.UNKNOWN,
        status=EventStatus.CORRECTED,
        supersedes_event_id=original.id,
    )

    state = machine.replay((original, correction))

    assert state.status == ROSCStatus.UNKNOWN
