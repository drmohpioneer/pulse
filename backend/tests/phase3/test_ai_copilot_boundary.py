from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.ai.copilot import CopilotRequest, CopilotResponse
from backend.services.demo_workflow import DemoWorkflowSession
from backend.workflow.events import EventStatus, EventType
from backend.workflow.rhythm import RhythmName


PULSE_ROOT = Path(__file__).resolve().parents[3]


class SnapshotMutatingCopilot:
    """Test double for an unsafe explanatory layer implementation."""

    def generate(self, request: CopilotRequest) -> CopilotResponse:
        rhythm_state = request.machine_states["rhythm"]
        shock_state = request.machine_states["shocks"]
        with pytest.raises(ValidationError):
            rhythm_state.current_rhythm = "AI_CHANGED_RHYTHM"
        with pytest.raises(ValidationError):
            shock_state.shock_count = 99
        return CopilotResponse(
            message="AI-visible text changed a snapshot only.",
            priority="low",
            reason="Attempted mutation was contained to immutable snapshots.",
            referenced_state_fields=("current_rhythm", "shock_count"),
        )


class TreatmentGeneratingCopilot:
    """Test double for an unsafe copilot that tries to issue treatment text."""

    def generate(self, request: CopilotRequest) -> CopilotResponse:
        return CopilotResponse(
            message="Deliver shock.",
            priority="high",
            reason=request.coordinator_decision.rationale,
            referenced_state_fields=("coordinator.primary_action",),
        )


def test_copilot_response_schema_is_explanatory_only() -> None:
    response = CopilotResponse(
        message="Explain the coordinator rationale.",
        priority="low",
        reason="State-grounded explanation.",
        referenced_state_fields=("coordinator.rationale",),
    )

    assert set(CopilotResponse.model_fields) == {
        "message",
        "priority",
        "reason",
        "referenced_state_fields",
        "referenced_event_ids",
        "source_recommendation_ids",
        "requires_confirmation",
        "safety_constraints",
    }
    assert not {
        "event",
        "events",
        "clinical_event",
        "event_type",
        "payload",
        "status",
        "confidence",
        "action_kind",
    }.intersection(response.model_dump())


def test_copilot_request_has_no_workflow_mutation_handles() -> None:
    assert not {
        "engine",
        "workflow_engine",
        "event_processor",
        "machine_registry",
        "routing_table",
        "state_machine",
        "process",
        "apply_event",
        "register_machine",
    }.intersection(CopilotRequest.model_fields)


def test_copilot_cannot_mutate_deterministic_workflow_through_state_snapshot() -> None:
    session = DemoWorkflowSession()
    session.process_action("vf")
    before = session.current_state()

    response = SnapshotMutatingCopilot().generate(_copilot_request(session))

    after = session.current_state()
    assert response.message == "AI-visible text changed a snapshot only."
    assert before.current_rhythm == "VF"
    assert before.shock_count == 0
    assert after.rhythm_state.current_rhythm == RhythmName.VF
    assert after.current_rhythm == "VF"
    assert after.shock_count == 0
    assert after.primary_action == "Deliver shock."


def test_copilot_treatment_text_does_not_enter_engine_or_change_state() -> None:
    session = DemoWorkflowSession()
    initial = session.current_state()

    response = TreatmentGeneratingCopilot().generate(_copilot_request(session))

    after = session.current_state()
    assert response.message == "Deliver shock."
    assert after.current_workflow_phase == initial.current_workflow_phase
    assert after.rhythm_state == initial.rhythm_state
    assert after.shock_state == initial.shock_state
    assert after.timeline == []


def test_copilot_cannot_modify_accepted_clinical_events() -> None:
    session = DemoWorkflowSession()
    session.process_action("vf")
    accepted_event = _copilot_request(session).accepted_events[0]

    assert accepted_event is not None
    assert accepted_event.event_type == EventType.RHYTHM_CHECKED
    assert accepted_event.status == EventStatus.ACCEPTED

    with pytest.raises(ValidationError):
        accepted_event.status = EventStatus.REJECTED  # type: ignore[misc]

    with pytest.raises(TypeError):
        accepted_event.payload["rhythm"] = "pea"


def test_ai_copilot_module_does_not_import_engine_or_state_machines() -> None:
    source = (PULSE_ROOT / "backend" / "ai" / "copilot.py").read_text()
    tree = ast.parse(source)
    imported_modules: set[str] = set()
    imported_names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_modules.add(node.module)
            imported_names.update(alias.name for alias in node.names)

    forbidden_modules = {
        "backend.workflow.engine",
        "backend.workflow.event_processor",
        "backend.workflow.rhythm",
        "backend.workflow.cpr",
        "backend.workflow.shocks",
        "backend.workflow.medications",
        "backend.workflow.rosc",
        "backend.workflow.hs_ts",
    }
    forbidden_names = {
        "ClinicalWorkflowEngine",
        "EventProcessor",
        "MachineRegistry",
        "RoutingTable",
        "RhythmStateMachine",
        "CPRStateMachine",
        "ShockStateMachine",
        "MedicationStateMachine",
        "ROSCStateMachine",
        "ReversibleCauseStateMachine",
    }

    assert imported_modules.isdisjoint(forbidden_modules)
    assert imported_names.isdisjoint(forbidden_names)


def test_copilot_contract_uses_explicit_allowed_inputs_only() -> None:
    assert set(CopilotRequest.model_fields) == {
        "coordinator_decision",
        "timeline",
        "accepted_events",
        "machine_states",
    }


def test_copilot_request_uses_immutable_snapshot_containers() -> None:
    request = _copilot_request(DemoWorkflowSession())

    with pytest.raises(ValidationError):
        request.accepted_events = ()  # type: ignore[misc]

    with pytest.raises(TypeError):
        request.machine_states["ai_added_state"] = object()


def _copilot_request(session: DemoWorkflowSession) -> CopilotRequest:
    state = session.current_state()
    accepted_events = tuple(session._timeline)
    return CopilotRequest(
        coordinator_decision=state.coordinator_decision,
        timeline=accepted_events,
        accepted_events=accepted_events,
        machine_states={
            "rhythm": state.rhythm_state,
            "shocks": state.shock_state,
            "medications": state.medication_state,
            "rosc": state.rosc_state,
        },
    )
