import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.workflow.events import ClinicalEvent


class DemoAuditStore:
    """Append-only JSONL audit store for demo sessions.

    This is intentionally local and minimal. It is not a production clinical
    record, PHI store, or compliance boundary.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        default_path = Path(tempfile.gettempdir()) / "pulse_demo_audit.jsonl"
        self.path = Path(path or os.getenv("PULSE_DEMO_AUDIT_PATH") or default_path)

    def append(
        self,
        *,
        session_id: str,
        record_type: str,
        payload: dict[str, Any],
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "recorded_at": datetime.now(UTC).isoformat(),
            "session_id": session_id,
            "record_type": record_type,
            "payload": payload,
        }
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            file.write("\n")

    def records(self, session_id: str | None = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        items: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                record = json.loads(line)
                if session_id is None or record.get("session_id") == session_id:
                    items.append(record)
        return items

    def accepted_or_corrected_events(
        self,
        session_id: str,
    ) -> list[ClinicalEvent]:
        records = self.records(session_id)
        undone_event_ids = {
            str(payload["event_id"])
            for record in records
            if record.get("record_type") == "auto_accepted_event_undone"
            if isinstance((payload := record.get("payload")), dict)
            and payload.get("event_id") is not None
        }
        events: list[ClinicalEvent] = []
        for record in records:
            if record.get("record_type") != "engine_event_processed":
                continue
            payload = record.get("payload", {})
            if payload.get("engine_eligible") is not True:
                continue
            raw_event = payload.get("event")
            if isinstance(raw_event, dict):
                event = ClinicalEvent.model_validate(raw_event)
                if str(event.id) not in undone_event_ids:
                    events.append(event)
        return events
