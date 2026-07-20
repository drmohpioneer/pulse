from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.workflow.events import EventType


class ObservationKind(StrEnum):
    COMPLETED_ACTION = "completed_action"
    RHYTHM_IDENTIFICATION = "rhythm_identification"
    COMMAND = "command"
    INTENT = "intent"
    OBSERVATION = "observation"
    CORRECTION = "correction"


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    timestamp: datetime
    speaker_label: str | None = None


class ExtractedClinicalObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: EventType
    payload: Mapping[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    observation_kind: ObservationKind
    raw_text: str
    speaker_label: str | None = None
    timestamp: datetime


class ACLSTranscriptEventExtractor:
    """Deterministic ACLS phrase extractor for simulated transcript segments.

    This is not speech recognition and does not call AI. It receives already
    transcribed text and emits structured observations for the fusion layer.
    """

    def extract(self, segment: TranscriptSegment) -> tuple[ExtractedClinicalObservation, ...]:
        text = _normalize(segment.text)
        observations: list[ExtractedClinicalObservation] = []

        rhythm = _rhythm_from_text(text)
        if rhythm is not None:
            observations.append(
                self._observation(
                    segment=segment,
                    event_type=EventType.RHYTHM_CHECKED,
                    payload={"rhythm": rhythm},
                    confidence=_scaled_confidence(segment.confidence, 0.96),
                    observation_kind=ObservationKind.RHYTHM_IDENTIFICATION,
                )
            )

        medication = _medication_from_text(text)
        if medication is not None:
            completed = _contains_any(text, ("given", "administered", "pushed", "is in"))
            dose = _dose_for(medication, text)
            payload: dict[str, Any] = {"medication": medication}
            if dose is not None:
                payload.update({"dose": dose, "unit": "mg", "route": "IV/IO"})
            observations.append(
                self._observation(
                    segment=segment,
                    event_type=EventType.MEDICATION_GIVEN,
                    payload=payload,
                    confidence=_scaled_confidence(segment.confidence, 0.95 if completed else 0.72),
                    observation_kind=(
                        ObservationKind.COMPLETED_ACTION
                        if completed
                        else ObservationKind.COMMAND
                    ),
                )
            )

        if _contains_any(text, ("shock delivered", "shocked", "defibrillated")):
            observations.append(
                self._observation(
                    segment=segment,
                    event_type=EventType.SHOCK_DELIVERED,
                    payload={},
                    confidence=_scaled_confidence(segment.confidence, 0.97),
                    observation_kind=ObservationKind.COMPLETED_ACTION,
                )
            )

        if _contains_any(text, ("cpr started", "start cpr", "compressions started", "start compressions")):
            observations.append(
                self._observation(
                    segment=segment,
                    event_type=EventType.CPR_STARTED,
                    payload={},
                    confidence=_scaled_confidence(segment.confidence, 0.94),
                    observation_kind=ObservationKind.COMPLETED_ACTION,
                )
            )

        if _contains_any(
            text,
            (
                "resume cpr",
                "resume compressions",
                "compressions resumed",
                "continue cpr",
                "continue compressions",
                "continue the cpr",
                "back on the chest",
                "compressions back on",
                "carry on cpr",
                "go back on cpr",
            ),
        ):
            observations.append(
                self._observation(
                    segment=segment,
                    event_type=EventType.CPR_RESUMED,
                    payload={},
                    confidence=_scaled_confidence(segment.confidence, 0.94),
                    observation_kind=ObservationKind.COMPLETED_ACTION,
                )
            )

        if _contains_any(text, ("pause cpr", "pause compressions", "compressions paused")):
            observations.append(
                self._observation(
                    segment=segment,
                    event_type=EventType.CPR_PAUSED,
                    payload={},
                    confidence=_scaled_confidence(segment.confidence, 0.90),
                    observation_kind=ObservationKind.COMPLETED_ACTION,
                )
            )

        if _contains_any(text, ("rosc", "return of spontaneous circulation")):
            observations.append(
                self._observation(
                    segment=segment,
                    event_type=EventType.ROSC_ACHIEVED,
                    payload={"rhythm": "rosc"},
                    confidence=_scaled_confidence(segment.confidence, 0.96),
                    observation_kind=ObservationKind.COMPLETED_ACTION,
                )
            )

        return tuple(observations)

    @staticmethod
    def _observation(
        *,
        segment: TranscriptSegment,
        event_type: EventType,
        payload: Mapping[str, Any],
        confidence: float,
        observation_kind: ObservationKind,
    ) -> ExtractedClinicalObservation:
        return ExtractedClinicalObservation(
            event_type=event_type,
            payload=payload,
            confidence=confidence,
            observation_kind=observation_kind,
            raw_text=segment.text,
            speaker_label=segment.speaker_label,
            timestamp=segment.timestamp,
        )


def _normalize(value: str) -> str:
    return " ".join(value.casefold().replace("-", " ").split())


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(_contains_phrase(text, phrase) for phrase in phrases)


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def _rhythm_from_text(text: str) -> str | None:
    if _contains_any(text, ("asystole",)):
        return "asystole"
    if _contains_any(text, ("pea", "pulseless electrical activity")):
        return "pea"
    if _contains_any(text, ("pulseless vt", "pvt")):
        return "pulseless_vt"
    if _contains_any(text, ("vf", "ventricular fibrillation")):
        return "vf"
    return None


def _medication_from_text(text: str) -> str | None:
    if _contains_any(text, ("epinephrine", "adrenaline", "epi")):
        return "epinephrine"
    if _contains_any(text, ("amiodarone", "amio")):
        return "amiodarone"
    if _contains_any(text, ("lidocaine", "lido")):
        return "lidocaine"
    return None


def _dose_for(medication: str, text: str) -> float | None:
    if medication == "epinephrine" and _contains_any(text, ("one milligram", "1 mg")):
        return 1.0
    if medication == "amiodarone" and _contains_any(text, ("300", "three hundred")):
        return 300.0
    if medication == "lidocaine" and _contains_any(text, ("100",)):
        return 100.0
    return None


def _scaled_confidence(segment_confidence: float, extraction_confidence: float) -> float:
    return round(max(0.0, min(1.0, segment_confidence * extraction_confidence)), 4)
