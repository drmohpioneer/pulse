"""Event Processor skeleton: deterministic dispatch of clinical events.

This module wires clinical events to the state machines that care about them.
It is pure architecture. It contains:

- NO ACLS / medical logic (which machine does what is decided by the machines
  and by an injected routing table, not here)
- NO AI
- NO workflow recommendations

Responsibilities implemented here:

1. Receive a ClinicalEvent.
2. Ask a RoutingTable which machine NAMES should receive the event.
3. Ask a MachineRegistry to resolve those names into machine INSTANCES.
4. Dispatch the event to every routed machine.
5. Keep going even if one machine raises.
6. Collect errors instead of stopping.
7. Return a ProcessingResult.

Dependency injection is used throughout. The EventProcessor never constructs a
concrete state machine; it only talks to abstractions (the registry, the routing
table, and the structural DispatchableMachine protocol).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from backend.workflow.events import ClinicalEvent, EventType


@runtime_checkable
class DispatchableMachine(Protocol):
    """Minimal structural contract the processor needs from a state machine.

    The processor only ever calls ``apply_event``. Depending on this small
    Protocol (rather than the concrete ``BaseWorkflowStateMachine``) keeps the
    processor decoupled and lets tests inject lightweight fakes.
    """

    def apply_event(self, event: ClinicalEvent) -> Any: ...


class MachineNotFoundError(LookupError):
    """Raised when a routed machine name is not present in the registry."""

    def __init__(self, name: str) -> None:
        super().__init__(f"No machine registered under name: {name!r}")
        self.name = name


# ---------------------------------------------------------------------------
# Routing table: maps an event type to the machine NAMES that should receive it
# ---------------------------------------------------------------------------


class RoutingTable:
    """Deterministic map of EventType -> ordered machine names.

    This is configuration/data, not clinical logic. It answers only "which
    machines are interested in this kind of event", never "what should happen
    clinically". It starts empty and is populated by the caller (dependency
    injection), so no clinical routing is hardcoded in this layer.
    """

    def __init__(
        self, routes: Mapping[EventType, Sequence[str]] | None = None
    ) -> None:
        self._routes: dict[EventType, tuple[str, ...]] = {}
        if routes:
            for event_type, names in routes.items():
                self.add_route(event_type, names)

    def add_route(self, event_type: EventType, machine_names: Iterable[str]) -> None:
        """Register the machine names that should receive an event type.

        Order is preserved and duplicates are removed so dispatch order is
        deterministic.
        """
        deduped: list[str] = []
        for name in machine_names:
            if name not in deduped:
                deduped.append(name)
        self._routes[event_type] = tuple(deduped)

    def machines_for(self, event: ClinicalEvent) -> tuple[str, ...]:
        """Return the ordered machine names routed for this event.

        An event type with no route returns an empty tuple (not an error).
        """
        return self._routes.get(event.event_type, ())

    def has_route(self, event_type: EventType) -> bool:
        return event_type in self._routes


# ---------------------------------------------------------------------------
# Machine registry: resolves machine NAMES into machine INSTANCES
# ---------------------------------------------------------------------------


class MachineRegistry:
    """Name -> machine instance lookup, populated by injection.

    The registry owns the machine instances. The processor borrows them by name.
    This is where dependency injection happens: whoever builds the system
    registers concrete machines here, and the processor stays ignorant of the
    concrete types.
    """

    def __init__(
        self, machines: Mapping[str, DispatchableMachine] | None = None
    ) -> None:
        self._machines: dict[str, DispatchableMachine] = dict(machines or {})

    def register(self, name: str, machine: DispatchableMachine) -> None:
        """Register (or replace) a machine under a name."""
        self._machines[name] = machine

    def has(self, name: str) -> bool:
        return name in self._machines

    def get(self, name: str) -> DispatchableMachine:
        """Resolve a name to its machine, or raise MachineNotFoundError."""
        try:
            return self._machines[name]
        except KeyError:
            raise MachineNotFoundError(name) from None

    def names(self) -> tuple[str, ...]:
        return tuple(self._machines.keys())


# ---------------------------------------------------------------------------
# Result objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcessingError:
    """One failure recorded during processing, without stopping the run.

    ``stage`` distinguishes a resolution failure (name not in registry) from a
    dispatch failure (the machine raised while applying the event).
    """

    machine_name: str
    stage: str  # "resolve" or "dispatch"
    error: BaseException

    @property
    def error_type(self) -> str:
        return type(self.error).__name__

    @property
    def message(self) -> str:
        return str(self.error)


@dataclass(frozen=True)
class ProcessingResult:
    """Outcome of processing a single event.

    - ``routed_machines``: names the routing table selected
    - ``succeeded_machines``: names that applied the event without error
    - ``states``: resulting state per succeeded machine (opaque to this layer)
    - ``errors``: collected failures (resolve or dispatch), in encounter order
    """

    event_id: UUID
    routed_machines: tuple[str, ...]
    succeeded_machines: tuple[str, ...]
    states: Mapping[str, Any]
    errors: tuple[ProcessingError, ...]

    @property
    def ok(self) -> bool:
        """True when every routed machine processed the event without error."""
        return not self.errors


# ---------------------------------------------------------------------------
# Event processor
# ---------------------------------------------------------------------------


class EventProcessor:
    """Routes a clinical event to its state machines and collects the outcome.

    Dependencies (routing table and machine registry) are injected. The
    processor never instantiates a concrete state machine.
    """

    def __init__(
        self, registry: MachineRegistry, routing_table: RoutingTable
    ) -> None:
        self._registry = registry
        self._routing = routing_table

    def process(self, event: ClinicalEvent) -> ProcessingResult:
        """Dispatch one event to every routed machine, collecting errors.

        Processing is best-effort per machine: a failure in one machine (missing
        registration or an exception during ``apply_event``) is recorded and
        processing continues with the remaining machines.
        """
        routed = self._routing.machines_for(event)
        succeeded: list[str] = []
        states: dict[str, Any] = {}
        errors: list[ProcessingError] = []

        for name in routed:
            # Resolve the name to an instance. A missing machine is a recorded
            # error, not a crash.
            if not self._registry.has(name):
                errors.append(
                    ProcessingError(
                        machine_name=name,
                        stage="resolve",
                        error=MachineNotFoundError(name),
                    )
                )
                continue

            machine = self._registry.get(name)

            # Dispatch. Catch Exception (not BaseException) so one misbehaving
            # machine cannot abort the whole run, while KeyboardInterrupt and
            # SystemExit still propagate.
            try:
                state = machine.apply_event(event)
            except Exception as exc:  # noqa: BLE001 - deliberate error collection
                errors.append(
                    ProcessingError(
                        machine_name=name, stage="dispatch", error=exc
                    )
                )
                continue

            succeeded.append(name)
            states[name] = state

        return ProcessingResult(
            event_id=event.id,
            routed_machines=routed,
            succeeded_machines=tuple(succeeded),
            states=MappingProxyType(states),
            errors=tuple(errors),
        )
