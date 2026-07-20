from datetime import UTC, datetime, timedelta
from unittest import TestCase, main

from backend.workflow.events import (
    ClinicalEvent,
    EventSource,
    EventStatus,
    EventType,
    Evidence,
)
from backend.workflow.recommendations import RecommendationPriority
from backend.workflow.rhythm import (
    RhythmCategory,
    RhythmName,
    RhythmState,
    RhythmStateMachine,
)


def make_evidence() -> Evidence:
    return Evidence(
        source=EventSource.SIMULATED,
        evidence_type="test_rhythm",
        confidence=0.95,
    )


def make_event(
    event_type: EventType = EventType.RHYTHM_CHECKED,
    rhythm: str | None = None,
    status: EventStatus = EventStatus.ACCEPTED,
    timestamp: datetime | None = None,
    supersedes_event_id=None,
) -> ClinicalEvent:
    payload = {} if rhythm is None else {"rhythm": rhythm}
    return ClinicalEvent(
        event_type=event_type,
        source=EventSource.SIMULATED,
        confidence=0.95,
        status=status,
        evidence=(make_evidence(),),
        payload=payload,
        timestamp=timestamp or datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
        supersedes_event_id=supersedes_event_id,
    )


class RhythmStateMachineTest(TestCase):
    def test_initial_state_is_unknown(self) -> None:
        machine = RhythmStateMachine()

        state = machine.get_state()

        self.assertIsInstance(state, RhythmState)
        self.assertEqual(state.current_rhythm, RhythmName.UNKNOWN)
        self.assertEqual(state.current_category, RhythmCategory.UNKNOWN)
        self.assertEqual(state.applied_event_ids, ())

    def test_applies_vf_as_shockable_rhythm(self) -> None:
        machine = RhythmStateMachine()
        event = make_event(rhythm="vf")

        state = machine.apply_event(event)

        self.assertEqual(state.current_rhythm, RhythmName.VF)
        self.assertEqual(state.current_category, RhythmCategory.SHOCKABLE)
        self.assertEqual(state.last_checked_at_event_id, event.id)
        self.assertEqual(state.confidence, event.confidence)
        self.assertEqual(state.applied_event_ids, (event.id,))

    def test_applies_pulseless_vt_alias_as_shockable_rhythm(self) -> None:
        machine = RhythmStateMachine()
        event = make_event(rhythm="pulseless ventricular tachycardia")

        state = machine.apply_event(event)

        self.assertEqual(state.current_rhythm, RhythmName.PULSELESS_VT)
        self.assertEqual(state.current_category, RhythmCategory.SHOCKABLE)

    def test_applies_pea_and_asystole_as_nonshockable_rhythms(self) -> None:
        machine = RhythmStateMachine()

        pea_state = machine.apply_event(make_event(rhythm="pea"))
        asystole_state = machine.apply_event(make_event(rhythm="asystole"))

        self.assertEqual(pea_state.current_category, RhythmCategory.NON_SHOCKABLE)
        self.assertEqual(asystole_state.current_rhythm, RhythmName.ASYSTOLE)
        self.assertEqual(asystole_state.current_category, RhythmCategory.NON_SHOCKABLE)

    def test_applies_shockable_bool_without_inventing_exact_rhythm(self) -> None:
        event = ClinicalEvent(
            event_type=EventType.RHYTHM_CHECKED,
            source=EventSource.SIMULATED,
            confidence=0.95,
            status=EventStatus.ACCEPTED,
            evidence=(make_evidence(),),
            payload={"shockable": True},
            timestamp=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
        )

        state = RhythmStateMachine().apply_event(event)

        self.assertEqual(state.current_rhythm, RhythmName.SHOCKABLE_UNKNOWN)
        self.assertEqual(state.current_category, RhythmCategory.SHOCKABLE)

    def test_applies_rosc_event_as_rosc_state(self) -> None:
        machine = RhythmStateMachine()
        event = make_event(event_type=EventType.ROSC_ACHIEVED, rhythm=None)

        state = machine.apply_event(event)

        self.assertEqual(state.current_rhythm, RhythmName.ROSC)
        self.assertEqual(state.current_category, RhythmCategory.ROSC)

    def test_applies_organized_rhythm_as_organized_state(self) -> None:
        machine = RhythmStateMachine()
        event = make_event(rhythm="organized rhythm")

        state = machine.apply_event(event)

        self.assertEqual(state.current_rhythm, RhythmName.ORGANIZED)
        self.assertEqual(state.current_category, RhythmCategory.ORGANIZED)

    def test_ignores_unrelated_events(self) -> None:
        machine = RhythmStateMachine()
        initial = machine.get_state()
        unrelated = make_event(event_type=EventType.MEDICATION_GIVEN, rhythm="vf")

        state = machine.apply_event(unrelated)

        self.assertEqual(state, initial)
        self.assertEqual(machine.get_state(), initial)

    def test_ignores_events_that_are_not_accepted_or_corrected(self) -> None:
        machine = RhythmStateMachine()
        initial = machine.get_state()
        candidate = make_event(rhythm="vf", status=EventStatus.CANDIDATE)

        state = machine.apply_event(candidate)

        self.assertEqual(state, initial)

    def test_unknown_or_missing_payload_is_deterministic(self) -> None:
        machine = RhythmStateMachine()
        event = make_event(rhythm=None)

        state = machine.apply_event(event)

        self.assertEqual(state.current_rhythm, RhythmName.UNKNOWN)
        self.assertEqual(state.current_category, RhythmCategory.UNKNOWN)
        self.assertEqual(state.last_checked_at_event_id, event.id)

    def test_replay_resets_and_reconstructs_state_from_ordered_events(self) -> None:
        machine = RhythmStateMachine()
        first = make_event(rhythm="vf")
        second = make_event(
            rhythm="pea",
            timestamp=first.timestamp + timedelta(minutes=2),
        )

        state = machine.replay((first, second))

        self.assertEqual(state.current_rhythm, RhythmName.PEA)
        self.assertEqual(state.current_category, RhythmCategory.NON_SHOCKABLE)
        self.assertEqual(state.applied_event_ids, (first.id, second.id))

        replayed_again = machine.replay((first,))
        self.assertEqual(replayed_again.current_rhythm, RhythmName.VF)
        self.assertEqual(replayed_again.applied_event_ids, (first.id,))

    def test_replay_ignores_superseded_events(self) -> None:
        machine = RhythmStateMachine()
        original = make_event(rhythm="vf")
        correction = make_event(
            rhythm="asystole",
            status=EventStatus.CORRECTED,
            supersedes_event_id=original.id,
            timestamp=original.timestamp + timedelta(seconds=20),
        )

        state = machine.replay((original, correction))

        self.assertEqual(state.current_rhythm, RhythmName.ASYSTOLE)
        self.assertEqual(state.current_category, RhythmCategory.NON_SHOCKABLE)
        self.assertEqual(state.applied_event_ids, (correction.id,))

    def test_shockable_recommendation_is_deterministic(self) -> None:
        machine = RhythmStateMachine()
        machine.apply_event(make_event(rhythm="vf"))

        first = machine.get_recommendations()
        second = machine.get_recommendations()

        self.assertEqual(first, second)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].id, "rhythm.shockable.deliver_shock")
        self.assertEqual(first[0].priority, RecommendationPriority.CRITICAL)

    def test_nonshockable_recommendations_are_deterministic(self) -> None:
        machine = RhythmStateMachine()
        machine.apply_event(make_event(rhythm="asystole"))

        recommendations = machine.get_recommendations()

        self.assertEqual(
            [item.id for item in recommendations],
            ["rhythm.non_shockable.cpr"],
        )

    def test_rosc_recommendation_routes_to_post_cardiac_arrest_care(self) -> None:
        machine = RhythmStateMachine()
        machine.apply_event(make_event(event_type=EventType.ROSC_ACHIEVED))

        recommendations = machine.get_recommendations()

        self.assertEqual(
            [item.id for item in recommendations],
            ["rhythm.rosc.post_cardiac_arrest_care"],
        )

    def test_unknown_recommendation_requires_confirmation(self) -> None:
        machine = RhythmStateMachine()

        recommendations = machine.get_recommendations()

        self.assertEqual(recommendations[0].id, "rhythm.unknown.assess_rhythm")
        self.assertTrue(recommendations[0].requires_confirmation)

    def test_explain_references_current_rhythm_state_and_event(self) -> None:
        machine = RhythmStateMachine()
        event = make_event(rhythm="vf")
        machine.apply_event(event)

        explanation = machine.explain()

        self.assertIn(str(event.id), explanation.referenced_event_ids)
        self.assertIn("current_rhythm", explanation.referenced_state_fields)
        self.assertIn("shockable", explanation.summary)


if __name__ == "__main__":
    main()
