from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.ai.copilot import CopilotRequest
from backend.services.demo_workflow import DemoWorkflowSession
from backend.workflow.events import ClinicalEvent, EventSource, EventStatus, EventType, Evidence


def test_copilot_rejects_nonaccepted_events_as_input() -> None:
    session = DemoWorkflowSession()
    state = session.current_state()
    candidate_event = ClinicalEvent(
        event_type=EventType.RHYTHM_CHECKED,
        source=EventSource.SPEECH,
        confidence=0.7,
        status=EventStatus.NEEDS_CONFIRMATION,
        evidence=(
            Evidence(
                source=EventSource.SPEECH,
                evidence_type="test_candidate",
                confidence=0.7,
                raw_reference="Maybe VF",
            ),
        ),
        payload={"rhythm": "vf"},
        timestamp=datetime.now(UTC),
    )

    with pytest.raises(ValidationError):
        CopilotRequest(
            coordinator_decision=state.coordinator_decision,
            timeline=(candidate_event,),
            accepted_events=(),
            machine_states={"rhythm": state.rhythm_state},
        )


def test_copilot_note_does_not_mutate_demo_workflow_state_or_timeline() -> None:
    session = DemoWorkflowSession()
    session.process_action("vf")
    before = session.current_state()

    response = session.copilot_note()
    after = session.current_state()

    assert before.rhythm_state == after.rhythm_state
    assert before.shock_state == after.shock_state
    assert before.medication_state == after.medication_state
    assert before.rosc_state == after.rosc_state
    assert before.timeline == after.timeline
    assert response.message == "Deterministic workflow focus: Deliver shock."
    assert response.referenced_event_ids == tuple(event.id for event in before.timeline)


def test_copilot_response_is_explanatory_and_contains_no_clinical_event_output() -> None:
    session = DemoWorkflowSession()
    session.process_action("vf")

    response = session.copilot_note()
    dumped = response.model_dump()

    assert "event_type" not in dumped
    assert "payload" not in dumped
    assert "status" not in dumped
    assert response.source_recommendation_ids
    assert set(response.source_recommendation_ids).issubset(
        set(session.current_state().coordinator_decision.source_recommendation_ids)
    )
    assert (
        "copilot_does_not_create_or_modify_events"
        in response.safety_constraints
    )


def test_copilot_uses_coordinator_output_for_post_rosc_note_without_arrest_action() -> None:
    session = DemoWorkflowSession()
    for action in (
        "vf",
        "shock_delivered",
        "shock_delivered",
        "shock_delivered",
        "rosc",
    ):
        session.process_action(action)

    response = session.copilot_note()

    assert response.message == (
        "Deterministic workflow focus: Transition to post-cardiac arrest care."
    )
    assert "Deliver shock." not in response.message
    assert "Resume CPR." not in response.message
