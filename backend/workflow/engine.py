"""Single entry point for the deterministic clinical workflow system.

The engine is orchestration only. It composes the registry, routing table, and
event processor, then exposes a narrow API for processing events and inspecting
registered state machines.

This module contains:

- NO ACLS logic
- NO AI calls
- NO recommendation aggregation
- NO global workflow state
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from backend.workflow.event_processor import (
    DispatchableMachine,
    EventProcessor,
    MachineRegistry,
    ProcessingResult,
    RoutingTable,
)
from backend.workflow.events import ClinicalEvent


@runtime_checkable
class StatefulMachine(DispatchableMachine, Protocol):
    """Machine contract required for state inspection through the engine."""

    def get_state(self) -> Any: ...


class ClinicalWorkflowEngine:
    """Orchestration-only façade for deterministic workflow processing."""

    def __init__(
        self,
        registry: MachineRegistry | None = None,
        routing_table: RoutingTable | None = None,
        processor: EventProcessor | None = None,
    ) -> None:
        self._registry = registry or MachineRegistry()
        self._routing_table = routing_table or RoutingTable()
        self._processor = processor or EventProcessor(
            self._registry,
            self._routing_table,
        )

    @property
    def registry(self) -> MachineRegistry:
        """Expose the injected registry for advanced composition."""
        return self._registry

    @property
    def routing_table(self) -> RoutingTable:
        """Expose the injected routing table for route configuration."""
        return self._routing_table

    def process(self, event: ClinicalEvent) -> ProcessingResult:
        """Process one event through the deterministic workflow processor."""
        return self._processor.process(event)

    def register_machine(self, name: str, machine: DispatchableMachine) -> None:
        """Register or replace a deterministic workflow state machine."""
        self._registry.register(name, machine)

    def get_machine(self, name: str) -> DispatchableMachine:
        """Return a registered machine by name."""
        return self._registry.get(name)

    def get_machine_state(self, name: str) -> Any:
        """Return state for a registered machine that exposes get_state()."""
        machine = self.get_machine(name)
        get_state = getattr(machine, "get_state", None)
        if not callable(get_state):
            raise TypeError(f"Registered machine {name!r} does not expose get_state()")
        return get_state()
