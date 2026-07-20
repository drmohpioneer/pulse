from datetime import UTC, datetime
from unittest import TestCase, main

from backend.workflow.engine import ClinicalWorkflowEngine
from backend.workflow.event_processor import (
    DispatchableMachine,
    EventProcessor,
    MachineNotFoundError,
    MachineRegistry,
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
from backend.workflow.rhythm import RhythmCategory, RhythmName, RhythmStateMachine


def make_event(event_type: EventType = EventType.RHYTHM_CHECKED) -> ClinicalEvent:
    return ClinicalEvent(
        event_type=event_type,
        source=EventSource.SIMULATED,
        confidence=0.9,
        status=EventStatus.ACCEPTED,
        evidence=(
            Evidence(
                source=EventSource.SIMULATED,
                evidence_type="engine_test",
                confidence=0.9,
            ),
        ),
        timestamp=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
    )


def make_rhythm_event(rhythm: str) -> ClinicalEvent:
    return ClinicalEvent(
        event_type=EventType.RHYTHM_CHECKED,
        source=EventSource.SIMULATED,
        confidence=0.95,
        status=EventStatus.ACCEPTED,
        evidence=(
            Evidence(
                source=EventSource.SIMULATED,
                evidence_type="simulated_rhythm_check",
                confidence=0.95,
                payload={"rhythm": rhythm},
            ),
        ),
        payload={"rhythm": rhythm},
        timestamp=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
    )


class RecordingMachine:
    def __init__(self, state: object = "initial") -> None:
        self.received: list[ClinicalEvent] = []
        self._state = state

    def apply_event(self, event: ClinicalEvent) -> object:
        self.received.append(event)
        self._state = {"event_id": event.id, "event_type": event.event_type}
        return self._state

    def get_state(self) -> object:
        return self._state


class ApplyOnlyMachine:
    def __init__(self) -> None:
        self.received: list[ClinicalEvent] = []

    def apply_event(self, event: ClinicalEvent) -> object:
        self.received.append(event)
        return "applied"


class StubProcessor:
    def __init__(self) -> None:
        self.processed: list[ClinicalEvent] = []

    def process(self, event: ClinicalEvent) -> ProcessingResult:
        self.processed.append(event)
        return ProcessingResult(
            event_id=event.id,
            routed_machines=(),
            succeeded_machines=(),
            states={},
            errors=(),
        )


class ClinicalWorkflowEngineTest(TestCase):
    def test_default_engine_composes_registry_routing_table_and_processor(self) -> None:
        engine = ClinicalWorkflowEngine()

        self.assertIsInstance(engine.registry, MachineRegistry)
        self.assertIsInstance(engine.routing_table, RoutingTable)

    def test_register_machine_and_get_machine_use_registry(self) -> None:
        engine = ClinicalWorkflowEngine()
        machine = RecordingMachine()

        engine.register_machine("rhythm", machine)

        self.assertIs(engine.get_machine("rhythm"), machine)
        self.assertTrue(isinstance(machine, DispatchableMachine))

    def test_get_machine_raises_for_missing_machine(self) -> None:
        engine = ClinicalWorkflowEngine()

        with self.assertRaises(MachineNotFoundError):
            engine.get_machine("missing")

    def test_get_machine_state_returns_registered_machine_state(self) -> None:
        engine = ClinicalWorkflowEngine()
        machine = RecordingMachine(state={"current": "unknown"})
        engine.register_machine("rhythm", machine)

        self.assertEqual(engine.get_machine_state("rhythm"), {"current": "unknown"})

    def test_get_machine_state_rejects_apply_only_machine(self) -> None:
        engine = ClinicalWorkflowEngine()
        engine.register_machine("apply_only", ApplyOnlyMachine())

        with self.assertRaises(TypeError):
            engine.get_machine_state("apply_only")

    def test_process_delegates_to_event_processor(self) -> None:
        registry = MachineRegistry()
        routing_table = RoutingTable({EventType.RHYTHM_CHECKED: ["rhythm"]})
        engine = ClinicalWorkflowEngine(registry=registry, routing_table=routing_table)
        machine = RecordingMachine()
        engine.register_machine("rhythm", machine)
        event = make_event(EventType.RHYTHM_CHECKED)

        result = engine.process(event)

        self.assertIsInstance(result, ProcessingResult)
        self.assertTrue(result.ok)
        self.assertEqual(result.routed_machines, ("rhythm",))
        self.assertEqual(result.succeeded_machines, ("rhythm",))
        self.assertEqual(machine.received, [event])
        self.assertEqual(result.states["rhythm"], machine.get_state())

    def test_process_does_not_aggregate_unrouted_machine_state(self) -> None:
        engine = ClinicalWorkflowEngine(
            routing_table=RoutingTable({EventType.RHYTHM_CHECKED: ["rhythm"]})
        )
        routed_machine = RecordingMachine()
        unrelated_machine = RecordingMachine(state={"unchanged": True})
        engine.register_machine("rhythm", routed_machine)
        engine.register_machine("medications", unrelated_machine)

        result = engine.process(make_event(EventType.RHYTHM_CHECKED))

        self.assertEqual(tuple(result.states.keys()), ("rhythm",))
        self.assertEqual(engine.get_machine_state("medications"), {"unchanged": True})

    def test_preserves_injected_registry_and_routing_table_instances(self) -> None:
        registry = MachineRegistry()
        routing_table = RoutingTable()
        engine = ClinicalWorkflowEngine(registry=registry, routing_table=routing_table)

        machine = RecordingMachine()
        registry.register("rhythm", machine)
        routing_table.add_route(EventType.RHYTHM_CHECKED, ["rhythm"])

        result = engine.process(make_event(EventType.RHYTHM_CHECKED))

        self.assertEqual(result.succeeded_machines, ("rhythm",))
        self.assertEqual(machine.received[0].event_type, EventType.RHYTHM_CHECKED)

    def test_preserves_injected_processor(self) -> None:
        registry = MachineRegistry()
        routing_table = RoutingTable()
        processor = StubProcessor()
        engine = ClinicalWorkflowEngine(
            registry=registry,
            routing_table=routing_table,
            processor=processor,  # type: ignore[arg-type]
        )
        event = make_event()

        result = engine.process(event)

        self.assertEqual(processor.processed, [event])
        self.assertEqual(result.event_id, event.id)

    def test_register_machine_replaces_existing_machine(self) -> None:
        engine = ClinicalWorkflowEngine()
        first = RecordingMachine(state="first")
        second = RecordingMachine(state="second")

        engine.register_machine("rhythm", first)
        engine.register_machine("rhythm", second)

        self.assertIs(engine.get_machine("rhythm"), second)
        self.assertEqual(engine.get_machine_state("rhythm"), "second")

    def test_real_engine_processor_rhythm_machine_flow_for_vf(self) -> None:
        registry = MachineRegistry()
        routing_table = RoutingTable({EventType.RHYTHM_CHECKED: ["rhythm"]})
        engine = ClinicalWorkflowEngine(registry=registry, routing_table=routing_table)
        rhythm_machine = RhythmStateMachine()
        engine.register_machine("rhythm", rhythm_machine)

        result = engine.process(make_rhythm_event("vf"))

        self.assertTrue(result.ok)
        self.assertEqual(result.routed_machines, ("rhythm",))
        self.assertEqual(result.succeeded_machines, ("rhythm",))

        state = engine.get_machine_state("rhythm")
        self.assertEqual(state.current_rhythm, RhythmName.VF)
        self.assertEqual(state.current_category, RhythmCategory.SHOCKABLE)

        recommendations = rhythm_machine.get_recommendations()
        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0].message, "Deliver shock.")


if __name__ == "__main__":
    main()
