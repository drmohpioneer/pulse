"""Deterministic base contract for all clinical workflow state machines.

This module defines the shared framework every clinical state machine follows.
It intentionally contains NO medical logic, NO ACLS algorithm content, NO AI,
and NO UI code.

Framework responsibilities implemented here (deterministic, safe to share):

- state access            -> get_state()
- reset to a clean state  -> reset()
- rebuild from history    -> replay(events)

Medical responsibilities are declared as abstract hooks and left as
placeholders for each concrete machine to implement later:

- apply_event(event)      -> per-machine transition rules (ACLS logic, later)
- get_recommendations()   -> deterministic next actions from state (later)
- explain()               -> reasoning trace for the current state (later)

Design rules (see docs/state_machine_contract.md):

- State machines are deterministic: same events + same order = same state.
- No AI, UI, audio, API, or database access inside a state machine.
- Every recommendation must have a reason.
- Every state transition must be explainable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Generic, TypeVar

from pydantic import BaseModel

from backend.models.common import Explanation
from backend.workflow.events import ClinicalEvent
from backend.workflow.recommendations import Recommendation

StateT = TypeVar("StateT", bound=BaseModel)


class BaseWorkflowStateMachine(ABC, Generic[StateT]):
    """Common deterministic contract for every clinical workflow state machine.

    A concrete machine parameterizes this base with its own state model, e.g.
    ``class RhythmStateMachine(BaseWorkflowStateMachine[RhythmState])``.

    Subclasses MUST implement:

    - ``_initial_state()``    : build a fresh default state (framework, not medical)
    - ``apply_event(event)``  : medical transition rules (placeholder for now)
    - ``get_recommendations()``: deterministic next actions (placeholder for now)
    - ``explain()``           : reasoning trace (placeholder for now)

    The framework methods below must never contain medical, AI, or UI logic.
    """

    def __init__(self) -> None:
        self._state: StateT = self._initial_state()

    # ----- framework: deterministic, no medical logic ------------------------

    def get_state(self) -> StateT:
        """Return the current deterministic state snapshot."""
        return self._state.model_copy(deep=True)

    def reset(self) -> None:
        """Reset the machine to its initial state.

        Deterministic and medical-logic-free: it only rebuilds the default
        state model. Used directly and by ``replay`` before re-applying events.
        """
        self._state = self._initial_state()

    def replay(self, events: Iterable[ClinicalEvent]) -> StateT:
        """Rebuild state deterministically from an ordered event history.

        Resets first, then applies each event in order. This supports
        correction and auditability: correcting the event history and replaying
        yields a consistent state (docs/state_machine_contract.md).

        An empty history is valid and returns the initial state.
        """
        self.reset()
        for event in events:
            self.apply_event(event)
        return self.get_state()

    # ----- framework factory: builds default state (not medical logic) -------

    @abstractmethod
    def _initial_state(self) -> StateT:
        """Construct a fresh default instance of this machine's state model.

        This is framework wiring, not medical logic: it only returns the empty
        starting state (e.g. ``RhythmState()``).
        """
        ...

    # ----- medical hooks: placeholders, implemented after ACLS review --------

    @abstractmethod
    def apply_event(self, event: ClinicalEvent) -> StateT:
        """Apply a validated clinical event and return the updated state.

        MEDICAL LOGIC PLACEHOLDER. Concrete machines define their deterministic
        transition rules here once the ACLS state-machine contract is accepted.
        """
        ...

    @abstractmethod
    def get_recommendations(self) -> list[Recommendation]:
        """Return deterministic next actions derived from the current state.

        MEDICAL LOGIC PLACEHOLDER. Every recommendation must carry a reason.
        """
        ...

    @abstractmethod
    def explain(self) -> Explanation:
        """Return a reasoning trace for the current state and recommendations.

        MEDICAL LOGIC PLACEHOLDER. Deterministic only; must never call an LLM.
        """
        ...
