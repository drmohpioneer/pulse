from backend.tests.workflow.helpers import at, make_event
from backend.workflow.cpr import CPRStateMachine, CPRStatus
from backend.workflow.events import EventStatus, EventType


def test_cpr_started_tracks_active_cycle_and_recommends_initial_rhythm_assessment() -> None:
    machine = CPRStateMachine()

    state = machine.apply_event(make_event(EventType.CPR_STARTED))
    recommendations = machine.get_recommendations(as_of=at(minutes=1))

    assert state.status == CPRStatus.ACTIVE
    assert state.cycle_number == 1
    assert recommendations[0].id == "cpr.assess_rhythm"


def test_two_minute_cycle_completed_recommends_rhythm_assessment() -> None:
    machine = CPRStateMachine()
    machine.apply_event(make_event(EventType.CPR_STARTED))
    machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="vf"))

    recommendations = machine.get_recommendations(as_of=at(minutes=2))

    assert recommendations[0].id == "cpr.assess_rhythm"


def test_shock_delivery_requires_cpr_resume() -> None:
    machine = CPRStateMachine()
    machine.apply_event(make_event(EventType.CPR_STARTED))

    state = machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=1))
    recommendations = machine.get_recommendations(as_of=at(minutes=1, seconds=10))

    assert state.status == CPRStatus.PAUSED
    assert state.shock_pending_resume
    assert state.hands_off_elapsed_seconds(at(minutes=1, seconds=10)) == 10
    assert recommendations[0].id == "cpr.resume_cpr"


def test_hands_off_elapsed_is_only_available_while_cpr_paused() -> None:
    machine = CPRStateMachine()
    active = machine.apply_event(make_event(EventType.CPR_STARTED))
    assert active.hands_off_elapsed_seconds(at(seconds=20)) is None

    paused = machine.apply_event(make_event(EventType.CPR_PAUSED, minutes=1))
    assert paused.hands_off_elapsed_seconds(at(minutes=1, seconds=14)) == 14

    resumed = machine.apply_event(make_event(EventType.CPR_RESUMED, minutes=1, seconds=20))
    assert resumed.hands_off_elapsed_seconds(at(minutes=1, seconds=25)) is None


def test_cpr_resumed_starts_next_cycle_and_continues_cpr() -> None:
    machine = CPRStateMachine()
    machine.apply_event(make_event(EventType.CPR_STARTED))
    machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="vf", seconds=10))
    machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=1))

    state = machine.apply_event(make_event(EventType.CPR_RESUMED, minutes=1, seconds=5))
    recommendations = machine.get_recommendations(as_of=at(minutes=2))

    assert state.status == CPRStatus.ACTIVE
    assert state.cycle_number == 2
    assert not state.shock_pending_resume
    assert recommendations[0].id == "cpr.continue_cpr"


def test_rosc_suppresses_cpr_recommendations_until_later_cpr_restart() -> None:
    machine = CPRStateMachine()
    machine.apply_event(make_event(EventType.CPR_STARTED))
    machine.apply_event(make_event(EventType.ROSC_ACHIEVED, minutes=1))

    assert machine.get_recommendations(as_of=at(minutes=2)) == []

    machine.apply_event(make_event(EventType.CPR_STARTED, minutes=3))
    machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="vf", minutes=3, seconds=5))

    assert machine.get_state().status == CPRStatus.ACTIVE
    assert machine.get_recommendations(as_of=at(minutes=3, seconds=30))[0].id == "cpr.continue_cpr"


def test_cpr_replay_skips_superseded_events_without_reordering() -> None:
    machine = CPRStateMachine()
    pause = make_event(EventType.CPR_PAUSED, minutes=1)
    correction = make_event(
        EventType.CPR_RESUMED,
        minutes=1,
        seconds=10,
        status=EventStatus.CORRECTED,
        supersedes_event_id=pause.id,
    )

    state = machine.replay(
        (
            make_event(EventType.CPR_STARTED),
            pause,
            correction,
        )
    )

    assert state.status == CPRStatus.ACTIVE
    assert state.last_paused_event_id is None
    assert state.last_resumed_event_id == correction.id
