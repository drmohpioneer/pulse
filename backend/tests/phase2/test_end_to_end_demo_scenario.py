from backend.services.demo_workflow import DemoWorkflowSession
from backend.workflow.coordinator import WorkflowPhase
from backend.workflow.events import EventStatus
from backend.workflow.rhythm import RhythmName


def test_end_to_end_voice_demo_timeline_covers_requested_clinical_flow() -> None:
    session = DemoWorkflowSession()

    scenario = session.run_end_to_end_voice_scenario()

    assert [step.transcript for step in scenario.timeline] == [
        "VF detected",
        "Shock delivered",
        "Adrenaline 1 mg given",
        "Shock delivered",
        "Shock delivered",
        "Patient still in VF after shocks",
        "ROSC achieved",
    ]

    assert [step.fusion_decision for step in scenario.timeline] == [
        EventStatus.ACCEPTED,
        EventStatus.ACCEPTED,
        EventStatus.ACCEPTED,
        EventStatus.REJECTED,
        EventStatus.REJECTED,
        EventStatus.REJECTED,
        EventStatus.ACCEPTED,
    ]
    assert scenario.timeline[1].engine_state.shock_count == 1
    assert scenario.timeline[2].engine_state.medication_history == ["epinephrine 1 mg"]
    assert scenario.timeline[-1].engine_state.rosc_status == "Achieved"

    assert scenario.state.current_workflow_phase == WorkflowPhase.POST_CARDIAC_ARREST_CARE
    assert scenario.state.rhythm_state.current_rhythm == RhythmName.ROSC
    assert scenario.state.shock_count == 1
    assert scenario.state.medication_history == ["epinephrine 1 mg"]
    assert scenario.state.rosc_status == "Achieved"


def test_demo_timeline_exposes_evidence_confidence_fusion_event_state_and_recommendation() -> None:
    scenario = DemoWorkflowSession().run_end_to_end_voice_scenario()

    for step in scenario.timeline:
        assert step.evidence
        assert step.confidence is not None
        assert step.engine_state.rhythm
        assert step.recommendation
        assert step.rationale
        assert all(0 <= evidence.confidence <= 1 for evidence in step.evidence)
