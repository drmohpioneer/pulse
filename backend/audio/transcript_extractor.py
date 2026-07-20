from datetime import UTC, datetime
from typing import Any

from backend.audio.transcription import (
    ACLSTranscriptEventExtractor,
    TranscriptSegment,
)
from backend.workflow.events import ClinicalEvent, EventSource, EventStatus, Evidence


class TranscriptPhraseEventExtractor:
    """Converts simulated transcript evidence into candidate clinical events.

    The extractor is deterministic and does not call speech recognition or AI.
    It preserves the original evidence object and marks every output as a
    candidate so fusion/confirmation remains the acceptance boundary.
    """

    def __init__(self, extractor: ACLSTranscriptEventExtractor | None = None) -> None:
        self._extractor = extractor or ACLSTranscriptEventExtractor()

    def extract(self, evidence: Evidence) -> tuple[ClinicalEvent, ...]:
        text = _text_from_evidence(evidence)
        speaker_label = evidence.payload.get("speaker_label")
        segment = TranscriptSegment(
            text=text,
            confidence=evidence.confidence,
            timestamp=evidence.timestamp,
            speaker_label=str(speaker_label) if speaker_label is not None else None,
        )

        events: list[ClinicalEvent] = []
        for observation in self._extractor.extract(segment):
            events.append(
                ClinicalEvent(
                    event_type=observation.event_type,
                    source=evidence.source,
                    confidence=observation.confidence,
                    status=EventStatus.CANDIDATE,
                    evidence=(evidence,),
                    payload={
                        **dict(observation.payload),
                        "extraction_kind": observation.observation_kind.value,
                    },
                    timestamp=observation.timestamp,
                )
            )
        return tuple(events)


def _text_from_evidence(evidence: Evidence) -> str:
    if evidence.raw_reference:
        return evidence.raw_reference
    text = evidence.payload.get("text")
    if text is not None:
        return str(text)
    return ""

