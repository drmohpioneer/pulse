from typing import Protocol

from backend.workflow.events import ClinicalEvent


class ClinicalEventStore(Protocol):
    def append(self, event: ClinicalEvent) -> None:
        """Persist an accepted or candidate clinical event."""
        # TODO: Add persistence implementation after storage decision.
        ...

    def list_events(self) -> list[ClinicalEvent]:
        """Return clinical events in replay order."""
        # TODO: Ensure audit-safe ordering and correction handling.
        ...

