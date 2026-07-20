"""Contract tests for the clinical workflow state-machine framework.

These tests verify the shared architecture in ``backend/workflow/base.py`` and
that every concrete state machine conforms to it. They deliberately do NOT test
any medical logic (there is none yet): the medical hooks are placeholders and
are only asserted to exist and to raise ``NotImplementedError``.
"""

import pytest
from pydantic import BaseModel, ValidationError

from backend.workflow.airway import AirwayStateMachine
from backend.workflow.base import BaseWorkflowStateMachine
from backend.workflow.cpr import CPRStateMachine
from backend.workflow.events import (
    ClinicalEvent,
    EventSource,
    EventStatus,
    EventType,
    Evidence,
)
from backend.workflow.hs_ts import ReversibleCauseStateMachine
from backend.workflow.medications import MedicationStateMachine
from backend.workflow.rhythm import RhythmStateMachine
from backend.workflow.rosc import ROSCStateMachine
from backend.workflow.shocks import ShockStateMachine
from backend.workflow.timers import TimerStateMachine

# Every concrete clinical workflow state machine. Add new machines here so they
# are automatically held to the shared contract.
STATE_MACHINE_CLASSES = [
    RhythmStateMachine,
    MedicationStateMachine,
    ShockStateMachine,
    AirwayStateMachine,
    ReversibleCauseStateMachine,
    TimerStateMachine,
    ROSCStateMachine,
    CPRStateMachine,
]

CONTRACT_METHODS = (
    "apply_event",
    "get_state",
    "get_recommendations",
    "explain",
    "replay",
    "reset",
)

PLACEHOLDER_METHODS = (
    "apply_event",
    "get_recommendations",
    "explain",
)

PLACEHOLDER_STATE_MACHINE_CLASSES = [
    AirwayStateMachine,
    TimerStateMachine,
]


def _dummy_event() -> ClinicalEvent:
    """A minimal valid ClinicalEvent for exercising placeholder methods."""
    return ClinicalEvent(
        event_type=EventType.UNKNOWN,
        source=EventSource.SIMULATED,
        confidence=0.5,
        status=EventStatus.CANDIDATE,
        evidence=(
            Evidence(
                source=EventSource.SIMULATED,
                evidence_type="test",
                confidence=0.5,
            ),
        ),
    )


@pytest.mark.parametrize("machine_cls", STATE_MACHINE_CLASSES)
def test_state_machine_follows_shared_interface(machine_cls) -> None:
    machine = machine_cls()

    # Subclasses the shared base contract.
    assert isinstance(machine, BaseWorkflowStateMachine)

    # Exposes every method the contract requires, all callable.
    for method_name in CONTRACT_METHODS:
        assert callable(getattr(machine, method_name))

    # State access returns a serializable pydantic state model.
    assert isinstance(machine.get_state(), BaseModel)


@pytest.mark.parametrize("machine_cls", STATE_MACHINE_CLASSES)
def test_empty_replay_returns_initial_state(machine_cls) -> None:
    machine = machine_cls()
    baseline = machine.get_state()

    result = machine.replay([])

    # Empty history is valid and yields the initial state.
    assert isinstance(result, BaseModel)
    assert result == baseline
    assert machine.get_state() == baseline


@pytest.mark.parametrize("machine_cls", STATE_MACHINE_CLASSES)
def test_reset_restores_initial_state(machine_cls) -> None:
    machine = machine_cls()
    before = machine.get_state()

    machine.reset()
    after = machine.get_state()

    # Reset produces a fresh instance that is value-equal to the initial state.
    assert after == before
    assert after is not before


@pytest.mark.parametrize("machine_cls", STATE_MACHINE_CLASSES)
def test_get_state_returns_immutable_snapshot(machine_cls) -> None:
    machine = machine_cls()
    state = machine.get_state()
    first_field = next(iter(type(state).model_fields))

    with pytest.raises(ValidationError):
        setattr(state, first_field, "__external_mutation__")


@pytest.mark.parametrize("machine_cls", PLACEHOLDER_STATE_MACHINE_CLASSES)
def test_placeholder_methods_exist_and_are_unimplemented(machine_cls) -> None:
    machine = machine_cls()

    # Each medical hook exists as a placeholder that raises until real
    # deterministic logic is added.
    for method_name in PLACEHOLDER_METHODS:
        assert callable(getattr(machine, method_name))

    with pytest.raises(NotImplementedError):
        machine.apply_event(_dummy_event())

    with pytest.raises(NotImplementedError):
        machine.get_recommendations()

    with pytest.raises(NotImplementedError):
        machine.explain()


def test_base_cannot_be_instantiated_directly() -> None:
    # The base is an abstract contract, not a usable machine on its own.
    with pytest.raises(TypeError):
        BaseWorkflowStateMachine()  # type: ignore[abstract]
