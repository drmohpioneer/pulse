from datetime import UTC, datetime
from unittest import TestCase, main
from uuid import UUID

from pydantic import ValidationError

from backend.workflow.events import (
    ClinicalEvent,
    CorrectionRecord,
    EventSource,
    EventStatus,
    EventType,
    Evidence,
)


def make_evidence() -> Evidence:
    return Evidence(
        source=EventSource.MANUAL,
        evidence_type="manual_confirmation",
        confidence=1.0,
        payload={"nested": {"value": "confirmed"}},
    )


def make_event(**overrides: object) -> ClinicalEvent:
    data = {
        "event_type": EventType.SHOCK_DELIVERED,
        "source": EventSource.MANUAL,
        "confidence": 0.95,
        "status": EventStatus.ACCEPTED,
        "evidence": (make_evidence(),),
    }
    data.update(overrides)
    return ClinicalEvent(**data)


class ClinicalEventModelTest(TestCase):
    def test_clinical_event_generates_uuid_and_timestamps(self) -> None:
        event = make_event()

        self.assertIsInstance(event.id, UUID)
        self.assertIsNotNone(event.timestamp.tzinfo)
        self.assertIsNotNone(event.created_at.tzinfo)

    def test_clinical_event_requires_one_or_more_evidence_objects(self) -> None:
        with self.assertRaises(ValidationError):
            make_event(evidence=())

    def test_clinical_event_validates_confidence_score_bounds(self) -> None:
        with self.assertRaises(ValidationError):
            make_event(confidence=1.1)

        with self.assertRaises(ValidationError):
            make_event(confidence=-0.1)

    def test_clinical_event_requires_timezone_aware_timestamp(self) -> None:
        with self.assertRaises(ValidationError):
            make_event(timestamp=datetime(2026, 7, 18, 12, 0, 0))

    def test_clinical_event_is_immutable_after_creation(self) -> None:
        event = make_event()

        with self.assertRaises(ValidationError):
            event.status = EventStatus.REJECTED

        with self.assertRaises(AttributeError):
            event.evidence.append(make_evidence())

        with self.assertRaises(TypeError):
            event.payload["new_value"] = "not allowed"

    def test_evidence_is_immutable_after_creation(self) -> None:
        evidence = make_evidence()

        with self.assertRaises(ValidationError):
            evidence.confidence = 0.5

        with self.assertRaises(TypeError):
            evidence.payload["nested"] = "not allowed"

        with self.assertRaises(TypeError):
            evidence.payload["nested"]["value"] = "not allowed"

    def test_correction_creates_new_event_referencing_previous_event(self) -> None:
        original = make_event(status=EventStatus.ACCEPTED)
        correction_record = CorrectionRecord(
            corrected_by="human_operator",
            reason="Original event was confirmed as wrong.",
            previous_status=original.status,
            superseded_event_id=original.id,
        )

        corrected = make_event(
            status=EventStatus.CORRECTED,
            supersedes_event_id=original.id,
            correction_history=(correction_record,),
        )

        self.assertNotEqual(corrected.id, original.id)
        self.assertEqual(corrected.supersedes_event_id, original.id)
        self.assertTrue(corrected.supersedes_another_event)
        self.assertEqual(corrected.correction_history[0].superseded_event_id, original.id)

    def test_corrected_event_must_reference_superseded_event(self) -> None:
        with self.assertRaises(ValidationError):
            make_event(status=EventStatus.CORRECTED, supersedes_event_id=None)

    def test_model_dump_serializes_immutable_payload_as_plain_mapping(self) -> None:
        event = make_event(payload={"nested": {"value": "kept"}})

        dumped = event.model_dump()

        self.assertEqual(dumped["payload"], {"nested": {"value": "kept"}})


if __name__ == "__main__":
    main()
