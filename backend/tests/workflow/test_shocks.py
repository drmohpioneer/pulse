from backend.tests.workflow.helpers import make_event
from backend.workflow.events import EventStatus, EventType
from backend.workflow.rhythm import RhythmCategory, RhythmName
from backend.workflow.shocks import ShockStateMachine


def test_vf_rhythm_check_recommends_shock() -> None:
    machine = ShockStateMachine()

    state = machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="vf"))

    assert state.latest_rhythm_name == RhythmName.VF
    assert state.latest_rhythm_category == RhythmCategory.SHOCKABLE
    assert machine.get_recommendations()[0].id == "shocks.deliver_shock"


def test_shock_delivered_for_current_rhythm_check_suppresses_repeat_shock() -> None:
    machine = ShockStateMachine()
    machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="vf"))

    state = machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=1))

    assert state.shock_count == 1
    assert state.shock_delivered_for_current_rhythm_check
    assert machine.get_recommendations() == []


def test_persistent_vf_on_new_rhythm_check_recommends_next_shock() -> None:
    machine = ShockStateMachine()
    machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="vf"))
    machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=1))

    state = machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="vf", minutes=3))

    assert state.shock_count == 1
    assert not state.shock_delivered_for_current_rhythm_check
    assert machine.get_recommendations()[0].message == "Deliver shock."


def test_nonshockable_rhythm_does_not_recommend_shock() -> None:
    machine = ShockStateMachine()

    machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="pea"))

    assert machine.get_state().latest_rhythm_category == RhythmCategory.NON_SHOCKABLE
    assert machine.get_recommendations() == []


def test_corrected_duplicate_shock_is_ignored_during_replay() -> None:
    machine = ShockStateMachine()
    first = make_event(EventType.SHOCK_DELIVERED, minutes=1)
    duplicate = make_event(EventType.SHOCK_DELIVERED, minutes=1, seconds=2)
    correction = make_event(
        EventType.UNKNOWN,
        minutes=1,
        seconds=5,
        status=EventStatus.CORRECTED,
        supersedes_event_id=duplicate.id,
    )

    state = machine.replay(
        (
            make_event(EventType.RHYTHM_CHECKED, rhythm="vf"),
            first,
            duplicate,
            correction,
        )
    )

    assert state.shock_count == 1
    assert state.shock_history[0].event_id == first.id


def test_later_arrest_rhythm_after_rosc_can_recommend_shock_without_resetting_history() -> None:
    machine = ShockStateMachine()
    machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="vf"))
    machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=1))
    machine.apply_event(make_event(EventType.ROSC_ACHIEVED, minutes=2))

    state = machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="pulseless_vt", minutes=5))

    assert state.shock_count == 1
    assert not state.rosc_achieved
    assert machine.get_recommendations()[0].id == "shocks.deliver_shock"
