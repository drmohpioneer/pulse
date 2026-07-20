from unittest import TestCase, main

from backend.services.demo_workflow import DemoWorkflowSession
from backend.workflow.coordinator import ActionKind, WorkflowPhase
from backend.workflow.cpr import CPRStatus
from backend.workflow.rhythm import RhythmCategory, RhythmName


class DemoWorkflowSessionTest(TestCase):
    def test_vf_button_flow_uses_real_engine_state_machines_and_coordinator(self) -> None:
        session = DemoWorkflowSession()

        state = session.process_action("vf")

        self.assertEqual(state.rhythm_state.current_rhythm, RhythmName.VF)
        self.assertEqual(state.rhythm_state.current_category, RhythmCategory.SHOCKABLE)
        self.assertEqual(state.shock_state.latest_rhythm_category, RhythmCategory.SHOCKABLE)
        self.assertEqual(state.coordinator_decision.phase, WorkflowPhase.SHOCKABLE_ARREST)
        self.assertEqual(
            state.coordinator_decision.primary_action.action_kind,
            ActionKind.DELIVER_SHOCK,
        )
        self.assertEqual(state.primary_action, "Deliver shock.")
        self.assertEqual(state.timeline[0].label, "VF")

    def test_cpr_started_is_accepted_and_coordinator_requests_rhythm_assessment(self) -> None:
        session = DemoWorkflowSession()

        state = session.process_action("cpr_started")

        self.assertEqual(state.cpr_state.status, CPRStatus.ACTIVE)
        self.assertEqual(state.rhythm_state.current_rhythm, RhythmName.UNKNOWN)
        self.assertEqual(state.rhythm_state.current_category, RhythmCategory.UNKNOWN)
        self.assertEqual(
            state.coordinator_decision.primary_action.action_kind,
            ActionKind.ASSESS_RHYTHM,
        )
        self.assertEqual(state.timeline[0].label, "CPR Started")

    def test_demo_flow_reaches_real_post_shock_cpr_and_medication_state(self) -> None:
        session = DemoWorkflowSession()

        session.process_action("cpr_started")
        session.process_action("vf")
        post_shock = session.process_action("shock_delivered")
        resumed = session.process_action("cpr_resumed")
        session.process_action("vf")
        after_second_shock = session.process_action("shock_delivered")
        after_epinephrine = session.process_action("epinephrine_given")

        self.assertEqual(post_shock.coordinator_decision.phase, WorkflowPhase.POST_SHOCK_CPR)
        self.assertEqual(
            post_shock.coordinator_decision.primary_action.action_kind,
            ActionKind.RESUME_CPR,
        )
        self.assertIsNotNone(post_shock.cpr_hands_off_elapsed_seconds)
        self.assertEqual(resumed.cpr_state.status, CPRStatus.ACTIVE)
        self.assertIsNone(resumed.cpr_hands_off_elapsed_seconds)
        self.assertEqual(after_second_shock.shock_state.shock_count, 2)
        self.assertEqual(after_epinephrine.medication_state.epinephrine_count, 1)
        self.assertIn("epinephrine 1 mg", after_epinephrine.medication_history)

    def test_epinephrine_button_logs_medication_and_suppresses_due_recommendation(self) -> None:
        session = DemoWorkflowSession()

        session.process_action("cpr_started")
        session.process_action("pea")
        state = session.process_action("epinephrine_given")

        self.assertEqual(state.medication_state.epinephrine_count, 1)
        self.assertIn("epinephrine 1 mg", state.medication_history)
        self.assertNotEqual(state.primary_action, "Give epinephrine as soon as feasible.")
        self.assertNotIn(
            "rhythm.non_shockable.epinephrine_asap",
            [item.recommendation.id for item in state.recommendations],
        )

    def test_reset_clears_in_memory_demo_session(self) -> None:
        session = DemoWorkflowSession()

        session.process_action("vf")
        state = session.reset()

        self.assertEqual(state.timeline, [])
        self.assertEqual(state.rhythm_state.current_rhythm, RhythmName.UNKNOWN)
        self.assertEqual(state.medication_state.epinephrine_count, 0)


if __name__ == "__main__":
    main()
