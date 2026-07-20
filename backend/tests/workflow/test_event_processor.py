"""Tests for the Event Processor skeleton.

These tests exercise ONLY the dispatch architecture: routing, resolution,
best-effort dispatch, and error collection. They use fake machines so no
clinical logic is involved (there is none to test yet).
"""

import pytest

from backend.workflow.event_processor import (
    DispatchableMachine,
    EventProcessor,
    MachineNotFoundError,
    MachineRegistry,
    ProcessingError,
    ProcessingResult,
    RoutingTable,
)
from backend.workflow.events import (
    ClinicalEvent,
    EventSource,
    EventStatus,
    EventType,
    Evidence,
)


# --------------------------------------------------------------------------- #
# Fakes and helpers
# --------------------------------------------------------------------------- #


class RecordingMachine:
    """A fake machine that records every event it receives and returns a state."""

    def __init__(self) -> None:
        self.received: list[ClinicalEvent] = []

    def apply_event(self, event: ClinicalEvent):
        self.received.append(event)
        return f"state-after-{event.event_type.value}"


class ExplodingMachine:
    """A fake machine that always raises when dispatched to."""

    def __init__(self, exc: Exception | None = None) -> None:
        self.received: list[ClinicalEvent] = []
        self.exc = exc or RuntimeError("boom")

    def apply_event(self, event: ClinicalEvent):
        self.received.append(event)
        raise self.exc


def make_event(event_type: EventType = EventType.SHOCK_DELIVERED) -> ClinicalEvent:
    return ClinicalEvent(
        event_type=event_type,
        source=EventSource.SIMULATED,
        confidence=0.9,
        status=EventStatus.ACCEPTED,
        evidence=(
            Evidence(
                source=EventSource.SIMULATED,
                evidence_type="test",
                confidence=0.9,
            ),
        ),
    )


def build_processor(
    routes: dict[EventType, list[str]],
    machines: dict[str, DispatchableMachine],
) -> EventProcessor:
    registry = MachineRegistry(machines)
    table = RoutingTable(routes)
    return EventProcessor(registry, table)


# --------------------------------------------------------------------------- #
# RoutingTable
# --------------------------------------------------------------------------- #


def test_routing_table_returns_routed_names_in_order():
    table = RoutingTable({EventType.SHOCK_DELIVERED: ["shocks", "timers"]})
    event = make_event(EventType.SHOCK_DELIVERED)

    assert table.machines_for(event) == ("shocks", "timers")


def test_routing_table_unknown_event_type_returns_empty():
    table = RoutingTable()
    event = make_event(EventType.ROSC_ACHIEVED)

    assert table.machines_for(event) == ()
    assert table.has_route(EventType.ROSC_ACHIEVED) is False


def test_routing_table_dedupes_but_preserves_order():
    table = RoutingTable()
    table.add_route(EventType.CPR_STARTED, ["cpr", "timers", "cpr", "rhythm"])

    event = make_event(EventType.CPR_STARTED)
    assert table.machines_for(event) == ("cpr", "timers", "rhythm")


# --------------------------------------------------------------------------- #
# MachineRegistry
# --------------------------------------------------------------------------- #


def test_registry_registers_and_resolves():
    machine = RecordingMachine()
    registry = MachineRegistry()
    registry.register("shocks", machine)

    assert registry.has("shocks") is True
    assert registry.get("shocks") is machine
    assert registry.names() == ("shocks",)


def test_registry_raises_for_unknown_name():
    registry = MachineRegistry()

    with pytest.raises(MachineNotFoundError) as exc_info:
        registry.get("missing")

    assert exc_info.value.name == "missing"


def test_registry_register_replaces_existing():
    first, second = RecordingMachine(), RecordingMachine()
    registry = MachineRegistry({"shocks": first})
    registry.register("shocks", second)

    assert registry.get("shocks") is second


# --------------------------------------------------------------------------- #
# EventProcessor: happy path
# --------------------------------------------------------------------------- #


def test_dispatches_to_all_routed_machines():
    shocks, timers = RecordingMachine(), RecordingMachine()
    processor = build_processor(
        routes={EventType.SHOCK_DELIVERED: ["shocks", "timers"]},
        machines={"shocks": shocks, "timers": timers},
    )
    event = make_event(EventType.SHOCK_DELIVERED)

    result = processor.process(event)

    assert isinstance(result, ProcessingResult)
    assert result.event_id == event.id
    assert result.routed_machines == ("shocks", "timers")
    assert result.succeeded_machines == ("shocks", "timers")
    assert result.ok is True
    assert result.errors == ()
    # Both machines actually received the event.
    assert shocks.received == [event]
    assert timers.received == [event]
    # States are collected per machine.
    assert result.states["shocks"] == "state-after-shock_delivered"
    assert result.states["timers"] == "state-after-shock_delivered"


def test_unrouted_event_dispatches_to_nobody():
    shocks = RecordingMachine()
    processor = build_processor(
        routes={EventType.SHOCK_DELIVERED: ["shocks"]},
        machines={"shocks": shocks},
    )
    event = make_event(EventType.ROSC_ACHIEVED)  # no route

    result = processor.process(event)

    assert result.routed_machines == ()
    assert result.succeeded_machines == ()
    assert result.errors == ()
    assert result.ok is True
    assert shocks.received == []


# --------------------------------------------------------------------------- #
# EventProcessor: resilience and error collection
# --------------------------------------------------------------------------- #


def test_continues_after_a_machine_raises():
    a, boom, c = RecordingMachine(), ExplodingMachine(), RecordingMachine()
    processor = build_processor(
        routes={EventType.MEDICATION_GIVEN: ["a", "boom", "c"]},
        machines={"a": a, "boom": boom, "c": c},
    )
    event = make_event(EventType.MEDICATION_GIVEN)

    result = processor.process(event)

    # The machine after the failing one still ran (processing did not abort).
    assert a.received == [event]
    assert c.received == [event]
    assert result.succeeded_machines == ("a", "c")

    # The failure was collected, not raised.
    assert len(result.errors) == 1
    err = result.errors[0]
    assert err.machine_name == "boom"
    assert err.stage == "dispatch"
    assert err.error_type == "RuntimeError"
    assert err.message == "boom"
    assert result.ok is False


def test_missing_machine_is_collected_as_resolve_error():
    a = RecordingMachine()
    processor = build_processor(
        routes={EventType.RHYTHM_CHECKED: ["a", "ghost"]},
        machines={"a": a},  # "ghost" intentionally not registered
    )
    event = make_event(EventType.RHYTHM_CHECKED)

    result = processor.process(event)

    assert result.succeeded_machines == ("a",)
    assert a.received == [event]
    assert len(result.errors) == 1
    err = result.errors[0]
    assert err.machine_name == "ghost"
    assert err.stage == "resolve"
    assert isinstance(err.error, MachineNotFoundError)


def test_multiple_errors_are_all_collected():
    boom1, boom2 = ExplodingMachine(ValueError("x")), ExplodingMachine(KeyError("y"))
    processor = build_processor(
        routes={EventType.AIRWAY_SECURED: ["boom1", "missing", "boom2"]},
        machines={"boom1": boom1, "boom2": boom2},
    )
    event = make_event(EventType.AIRWAY_SECURED)

    result = processor.process(event)

    assert result.succeeded_machines == ()
    assert [(e.machine_name, e.stage) for e in result.errors] == [
        ("boom1", "dispatch"),
        ("missing", "resolve"),
        ("boom2", "dispatch"),
    ]
    assert result.ok is False


# --------------------------------------------------------------------------- #
# Dependency injection guarantees
# --------------------------------------------------------------------------- #


def test_processor_resolves_machines_lazily_from_the_registry():
    """DI: a machine registered AFTER the processor is built is still used.

    Proves the processor holds a reference to the injected registry and resolves
    at process time, rather than capturing concrete machines at construction.
    """
    registry = MachineRegistry()
    table = RoutingTable({EventType.CPR_STARTED: ["late"]})
    processor = EventProcessor(registry, table)

    # Registered only now, after the processor already exists.
    late = RecordingMachine()
    registry.register("late", late)

    result = processor.process(make_event(EventType.CPR_STARTED))

    assert result.succeeded_machines == ("late",)
    assert len(late.received) == 1


def test_processor_uses_injected_routing_table():
    """DI: changing the injected routing table changes dispatch."""
    machine = RecordingMachine()
    registry = MachineRegistry({"shocks": machine})
    table = RoutingTable()
    processor = EventProcessor(registry, table)

    # No route yet -> nobody receives it.
    processor.process(make_event(EventType.SHOCK_DELIVERED))
    assert machine.received == []

    # Add the route on the same injected table -> now it dispatches.
    table.add_route(EventType.SHOCK_DELIVERED, ["shocks"])
    processor.process(make_event(EventType.SHOCK_DELIVERED))
    assert len(machine.received) == 1


# --------------------------------------------------------------------------- #
# Determinism and result immutability
# --------------------------------------------------------------------------- #


def test_dispatch_order_is_deterministic():
    machines = {name: RecordingMachine() for name in ("m1", "m2", "m3")}
    processor = build_processor(
        routes={EventType.CPR_PAUSED: ["m1", "m2", "m3"]},
        machines=machines,
    )

    first = processor.process(make_event(EventType.CPR_PAUSED))
    second = processor.process(make_event(EventType.CPR_PAUSED))

    assert first.routed_machines == second.routed_machines == ("m1", "m2", "m3")
    assert first.succeeded_machines == second.succeeded_machines


def test_result_is_frozen():
    processor = build_processor(routes={}, machines={})
    result = processor.process(make_event())

    with pytest.raises(Exception):
        result.event_id = None  # type: ignore[misc]


def test_result_states_mapping_is_read_only():
    machine = RecordingMachine()
    processor = build_processor(
        routes={EventType.SHOCK_DELIVERED: ["shocks"]},
        machines={"shocks": machine},
    )
    result = processor.process(make_event(EventType.SHOCK_DELIVERED))

    with pytest.raises(TypeError):
        result.states["shocks"] = "tampered"  # MappingProxyType is read-only


# --------------------------------------------------------------------------- #
# ProcessingError shape
# --------------------------------------------------------------------------- #


def test_processing_error_exposes_type_and_message():
    err = ProcessingError(
        machine_name="x", stage="dispatch", error=ValueError("bad thing")
    )
    assert err.error_type == "ValueError"
    assert err.message == "bad thing"
