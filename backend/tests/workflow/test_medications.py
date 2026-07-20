from backend.tests.workflow.helpers import at, make_event
from backend.workflow.events import EventStatus, EventType
from backend.workflow.medications import MedicationStateMachine
from backend.workflow.rhythm import RhythmCategory


def test_pea_makes_epinephrine_due_without_shockable_entry() -> None:
    machine = MedicationStateMachine()

    machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="pea"))

    assert machine.get_state().latest_rhythm_category == RhythmCategory.NON_SHOCKABLE
    assert machine.get_recommendations(as_of=at())[0].id == "medications.give_epinephrine"


def test_after_second_shock_epinephrine_is_due_during_continuous_episode() -> None:
    machine = MedicationStateMachine()
    machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="vf"))
    machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=1))
    machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="vf", minutes=3))
    machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=4))

    recommendations = machine.get_recommendations(as_of=at(minutes=4, seconds=5))

    assert recommendations[0].id == "medications.give_epinephrine"


def test_epinephrine_given_suppresses_until_four_minutes_elapsed() -> None:
    machine = MedicationStateMachine()
    machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="pea"))
    machine.apply_event(
        make_event(
            EventType.MEDICATION_GIVEN,
            medication="Epinephrine",
            dose=1,
            unit="mg",
            route="IV/IO",
        )
    )

    assert machine.get_state().epinephrine_count == 1
    assert machine.get_recommendations(as_of=at(minutes=3, seconds=59)) == []
    assert machine.get_recommendations(as_of=at(minutes=4))[0].id == "medications.give_epinephrine"


def test_epinephrine_timing_persists_when_pea_transitions_to_vf() -> None:
    machine = MedicationStateMachine()
    machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="pea"))
    machine.apply_event(
        make_event(EventType.MEDICATION_GIVEN, medication="epinephrine", dose=1, unit="mg")
    )
    machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="vf", minutes=1))
    machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=2))

    assert machine.get_recommendations(as_of=at(minutes=3, seconds=59)) == []
    assert any(
        item.id == "medications.give_epinephrine"
        for item in machine.get_recommendations(as_of=at(minutes=4))
    )


def test_get_recommendations_does_not_mutate_medication_state() -> None:
    machine = MedicationStateMachine()
    machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="pea"))
    machine.apply_event(make_event(EventType.MEDICATION_GIVEN, medication="epinephrine", dose=1))
    before = machine.get_state()

    machine.get_recommendations(as_of=at(minutes=4))

    assert machine.get_state() == before


def test_after_third_shock_amiodarone_may_be_considered() -> None:
    machine = MedicationStateMachine()
    machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="vf"))
    machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=1))
    machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=2))
    machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=3))

    recommendation_ids = [item.id for item in machine.get_recommendations(as_of=at(minutes=3))]

    assert "medications.consider_amiodarone" in recommendation_ids


def test_amiodarone_recorded_with_payload_without_dose_calculation() -> None:
    machine = MedicationStateMachine()
    event = make_event(
        EventType.MEDICATION_GIVEN,
        medication="Amiodarone",
        dose=300,
        unit="mg",
        route="IV/IO",
    )

    state = machine.apply_event(event)

    assert state.amiodarone_count == 1
    assert state.administrations[0].dose == 300
    assert state.administrations[0].unit == "mg"
    assert state.administrations[0].event_id == event.id


def test_amiodarone_second_dose_is_not_recommended_until_fifth_shock() -> None:
    machine = MedicationStateMachine()
    machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="vf"))
    for minute in range(1, 4):
        machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=minute))
    machine.apply_event(
        make_event(
            EventType.MEDICATION_GIVEN,
            minutes=3,
            medication="amiodarone",
            dose=300,
            unit="mg",
        )
    )
    machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=4))

    assert "medications.consider_amiodarone" not in [
        item.id for item in machine.get_recommendations(as_of=at(minutes=4))
    ]

    machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=5))
    recommendation = next(
        item
        for item in machine.get_recommendations(as_of=at(minutes=5))
        if item.id == "medications.consider_amiodarone"
    )
    assert recommendation.message == "Consider amiodarone 150 mg."


def test_lidocaine_recorded_with_payload_without_dose_calculation() -> None:
    machine = MedicationStateMachine()
    event = make_event(
        EventType.MEDICATION_GIVEN,
        medication="Lidocaine",
        dose=100,
        unit="mg",
        route="IV/IO",
    )

    state = machine.apply_event(event)

    assert state.lidocaine_count == 1
    assert state.last_lidocaine_event_id == event.id
    assert state.administrations[0].medication_name == "lidocaine"
    assert state.administrations[0].dose == 100
    assert state.administrations[0].unit == "mg"


def test_lidocaine_normalization_is_case_insensitive() -> None:
    machine = MedicationStateMachine()

    state = machine.apply_event(make_event(EventType.MEDICATION_GIVEN, medication="LIDO"))

    assert state.lidocaine_count == 1
    assert state.administrations[0].medication_name == "lidocaine"


def test_lidocaine_does_not_change_epi_or_amiodarone_counts() -> None:
    machine = MedicationStateMachine()

    state = machine.apply_event(make_event(EventType.MEDICATION_GIVEN, medication="lidocaine"))

    assert state.lidocaine_count == 1
    assert state.epinephrine_count == 0
    assert state.amiodarone_count == 0


def test_lidocaine_not_recommended_before_refractory_shockable_rhythm() -> None:
    machine = MedicationStateMachine()
    machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="vf"))
    machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=1))
    machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=2))

    recommendation_ids = [item.id for item in machine.get_recommendations(as_of=at(minutes=2))]

    assert "medications.consider_lidocaine" not in recommendation_ids


def test_lidocaine_may_be_considered_after_third_shock() -> None:
    machine = MedicationStateMachine()
    machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="vf"))
    machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=1))
    machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=2))
    machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=3))

    recommendation_ids = [item.id for item in machine.get_recommendations(as_of=at(minutes=3))]

    assert "medications.consider_lidocaine" in recommendation_ids


def test_lidocaine_not_recommended_for_nonshockable_rhythm() -> None:
    machine = MedicationStateMachine()
    machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="pea"))
    machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=1))
    machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=2))
    machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=3))

    recommendation_ids = [item.id for item in machine.get_recommendations(as_of=at(minutes=3))]

    assert "medications.consider_lidocaine" not in recommendation_ids


def test_lidocaine_suppressed_after_rosc() -> None:
    machine = MedicationStateMachine()
    machine.apply_event(make_event(EventType.RHYTHM_CHECKED, rhythm="vf"))
    machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=1))
    machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=2))
    machine.apply_event(make_event(EventType.SHOCK_DELIVERED, minutes=3))
    machine.apply_event(make_event(EventType.ROSC_ACHIEVED, minutes=4))

    assert machine.get_recommendations(as_of=at(minutes=4)) == []


def test_unknown_medication_is_recorded_but_does_not_change_epi_or_amio_counts() -> None:
    machine = MedicationStateMachine()

    state = machine.apply_event(make_event(EventType.MEDICATION_GIVEN, medication="calcium"))

    assert state.administrations[0].medication_name == "calcium"
    assert state.epinephrine_count == 0
    assert state.amiodarone_count == 0
    assert state.lidocaine_count == 0


def test_medication_replay_skips_superseded_administration() -> None:
    machine = MedicationStateMachine()
    original = make_event(EventType.MEDICATION_GIVEN, medication="epinephrine", dose=1)
    correction = make_event(
        EventType.MEDICATION_GIVEN,
        medication="amiodarone",
        dose=300,
        status=EventStatus.CORRECTED,
        supersedes_event_id=original.id,
    )

    state = machine.replay((original, correction))

    assert state.epinephrine_count == 0
    assert state.amiodarone_count == 1
    assert state.lidocaine_count == 0
    assert state.administrations[0].event_id == correction.id


def test_lidocaine_replay_skips_superseded_administration() -> None:
    machine = MedicationStateMachine()
    original = make_event(EventType.MEDICATION_GIVEN, medication="lidocaine", dose=100)
    correction = make_event(
        EventType.MEDICATION_GIVEN,
        medication="epinephrine",
        dose=1,
        status=EventStatus.CORRECTED,
        supersedes_event_id=original.id,
    )

    state = machine.replay((original, correction))

    assert state.lidocaine_count == 0
    assert state.epinephrine_count == 1
    assert state.administrations[0].event_id == correction.id
