from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
import re
from typing import Any
import unicodedata
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.audio.transcription import (
    ACLSTranscriptEventExtractor,
    ObservationKind,
    TranscriptSegment,
)
from backend.workflow.events import EventSource, EventType, Evidence


class AudioChunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    device_id: str | None = None
    started_at: datetime
    ended_at: datetime
    sample_rate_hz: int | None = Field(default=None, gt=0)
    channel_count: int | None = Field(default=None, gt=0)
    audio_reference: str | None = None
    simulated_text: str | None = None
    metadata: Mapping[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_time_order(self) -> "AudioChunk":
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must be after started_at")
        return self


class VoiceActivitySegment(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    chunk_id: str
    started_at: datetime
    ended_at: datetime
    speech_confidence: float = Field(ge=0.0, le=1.0)
    noise_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    overlap_probability: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_time_order(self) -> "VoiceActivitySegment":
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must be after started_at")
        return self


class SpeakerTurn(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    segment_id: str
    speaker_id: str = "speaker_unknown"
    started_at: datetime
    ended_at: datetime
    diarization_confidence: float = Field(ge=0.0, le=1.0)
    is_overlapping: bool = False


class SpeakerRole(StrEnum):
    PHYSICIAN = "physician"
    NURSE = "nurse"
    RECORDER = "recorder"
    TEAM_LEADER = "team_leader"
    UNKNOWN = "unknown"


class SpeakerRoleHypothesis(BaseModel):
    model_config = ConfigDict(frozen=True)

    speaker_id: str
    role: SpeakerRole = SpeakerRole.UNKNOWN
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str | None = None


class TranscriptLanguage(StrEnum):
    ENGLISH = "en"
    EGYPTIAN_ARABIC = "ar_eg"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class MultilingualTranscriptSegment(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    turn_id: str | None = None
    speaker_id: str = "speaker_unknown"
    text: str
    language: TranscriptLanguage = TranscriptLanguage.UNKNOWN
    confidence: float = Field(ge=0.0, le=1.0)
    started_at: datetime
    ended_at: datetime
    role_hypothesis: SpeakerRoleHypothesis | None = None
    alternatives: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be empty")
        return value


class NormalizedClinicalObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    transcript_id: str
    event_type: EventType
    payload: Mapping[str, Any] = Field(default_factory=dict)
    observation_kind: ObservationKind
    confidence: float = Field(ge=0.0, le=1.0)
    raw_text: str
    normalized_text: str
    language: TranscriptLanguage = TranscriptLanguage.UNKNOWN
    speaker_id: str = "speaker_unknown"
    role_hypothesis: SpeakerRoleHypothesis | None = None
    timestamp: datetime


class AcousticObservationType(StrEnum):
    MONITOR_ALARM = "monitor_alarm"
    DEFIBRILLATOR_CHARGING = "defibrillator_charging"
    DEFIBRILLATOR_DISCHARGE = "defibrillator_discharge"
    SUCTION = "suction"
    VENTILATOR_ALARM = "ventilator_alarm"
    CPR_FEEDBACK = "cpr_feedback"


class AcousticObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    chunk_id: str
    observation_type: AcousticObservationType
    confidence: float = Field(ge=0.0, le=1.0)
    started_at: datetime
    ended_at: datetime
    raw_reference: str | None = None
    metadata: Mapping[str, Any] = Field(default_factory=dict)


class DiarizationPlaceholder:
    """Provider-neutral deterministic diarization placeholder.

    It creates one speech segment and one speaker turn for a chunk. Speaker
    identity is advisory metadata only and never affects clinical validity.
    """

    def segment(self, chunk: AudioChunk) -> tuple[VoiceActivitySegment, ...]:
        return (
            VoiceActivitySegment(
                id=str(uuid5(NAMESPACE_URL, f"pulse-vad:{chunk.id}")),
                chunk_id=chunk.id,
                started_at=chunk.started_at,
                ended_at=chunk.ended_at,
                speech_confidence=float(chunk.metadata.get("speech_confidence", 0.95)),
                overlap_probability=float(chunk.metadata.get("overlap_probability", 0.0)),
            ),
        )

    def diarize(self, chunk: AudioChunk) -> tuple[SpeakerTurn, ...]:
        segment = self.segment(chunk)[0]
        speaker_id = str(chunk.metadata.get("speaker_id") or "speaker_unknown")
        diarization_confidence = 0.5 if speaker_id == "speaker_unknown" else 0.8
        return (
            SpeakerTurn(
                id=str(uuid5(NAMESPACE_URL, f"pulse-speaker-turn:{segment.id}:{speaker_id}")),
                segment_id=segment.id,
                speaker_id=speaker_id,
                started_at=segment.started_at,
                ended_at=segment.ended_at,
                diarization_confidence=diarization_confidence,
                is_overlapping=segment.overlap_probability > 0.25,
            ),
        )


class DeterministicMedicalPhraseNormalizer:
    """Deterministic first-pass English/Egyptian Arabic clinical normalizer."""

    def normalize(
        self,
        segment: MultilingualTranscriptSegment,
    ) -> tuple[NormalizedClinicalObservation, ...]:
        normalized = _normalize_text(segment.text)
        phrase = _phrase_match(normalized)
        if phrase is None:
            extracted = ACLSTranscriptEventExtractor().extract(
                TranscriptSegment(
                    text=segment.text,
                    confidence=segment.confidence,
                    timestamp=segment.started_at,
                    speaker_label=segment.speaker_id,
                )
            )
            return tuple(
                NormalizedClinicalObservation(
                    transcript_id=segment.id,
                    event_type=observation.event_type,
                    payload=observation.payload,
                    observation_kind=observation.observation_kind,
                    confidence=observation.confidence,
                    raw_text=observation.raw_text,
                    normalized_text=normalized,
                    language=segment.language,
                    speaker_id=segment.speaker_id,
                    role_hypothesis=segment.role_hypothesis,
                    timestamp=observation.timestamp,
                )
                for observation in extracted
            )

        event_type, payload, observation_kind, extraction_confidence = phrase
        return (
            NormalizedClinicalObservation(
                transcript_id=segment.id,
                event_type=event_type,
                payload=payload,
                observation_kind=observation_kind,
                confidence=_scaled_confidence(segment.confidence, extraction_confidence),
                raw_text=segment.text,
                normalized_text=normalized,
                language=segment.language,
                speaker_id=segment.speaker_id,
                role_hypothesis=segment.role_hypothesis,
                timestamp=segment.started_at,
            ),
        )


class AcousticObservationPlaceholder:
    """Deterministic placeholder for future non-speech acoustic extraction."""

    def detect(self, chunk: AudioChunk) -> tuple[AcousticObservation, ...]:
        raw_type = chunk.metadata.get("acoustic_observation_type")
        if raw_type is None:
            return ()
        observation_type = AcousticObservationType(str(raw_type))
        return (
            AcousticObservation(
                chunk_id=chunk.id,
                observation_type=observation_type,
                confidence=float(chunk.metadata.get("acoustic_confidence", 0.75)),
                started_at=chunk.started_at,
                ended_at=chunk.ended_at,
                raw_reference=str(raw_type),
            ),
        )


def clinical_observation_to_evidence(
    observation: NormalizedClinicalObservation,
) -> Evidence:
    role = observation.role_hypothesis.role.value if observation.role_hypothesis else None
    role_confidence = (
        observation.role_hypothesis.confidence if observation.role_hypothesis else None
    )
    payload = dict(observation.payload)
    is_positive = bool(payload.pop("is_positive", True))
    return Evidence(
        source=EventSource.SPEECH,
        evidence_type="normalized_clinical_observation",
        timestamp=observation.timestamp,
        confidence=observation.confidence,
        payload={
            "event_type": observation.event_type.value,
            "payload": payload,
            "observation_kind": observation.observation_kind.value,
            "is_positive": is_positive,
            "language": observation.language.value,
            "speaker_id": observation.speaker_id,
            "role": role,
            "role_confidence": role_confidence,
            "normalized_text": observation.normalized_text,
        },
        raw_reference=observation.raw_text,
    )


def acoustic_observation_to_evidence(observation: AcousticObservation) -> Evidence:
    return Evidence(
        source=EventSource.ACOUSTIC,
        evidence_type=observation.observation_type.value,
        timestamp=observation.started_at,
        confidence=observation.confidence,
        payload={
            "observation_type": observation.observation_type.value,
            "started_at": observation.started_at.isoformat(),
            "ended_at": observation.ended_at.isoformat(),
            **dict(observation.metadata),
        },
        raw_reference=observation.raw_reference or observation.observation_type.value,
    )


def _phrase_match(
    normalized: str,
) -> tuple[EventType, dict[str, Any], ObservationKind, float] | None:
    medication_match = _medication_phrase_match(normalized)
    if medication_match is not None:
        return medication_match
    if normalized in _SHOCK_COMPLETED:
        return _match(EventType.SHOCK_DELIVERED, {}, ObservationKind.COMPLETED_ACTION, 0.93)
    if normalized in _SHOCK_COMMANDS:
        return _match(EventType.SHOCK_DELIVERED, {}, ObservationKind.COMMAND, 0.88)
    if normalized in _SHOCK_PREPARATION_INTENTS:
        return _match(
            EventType.SHOCK_DELIVERED,
            {"shock_preparation": normalized},
            ObservationKind.INTENT,
            0.55,
        )
    if normalized in _CPR_STARTED_COMMANDS:
        return _match(EventType.CPR_STARTED, {}, ObservationKind.COMPLETED_ACTION, 0.9)
    if normalized in _CPR_STARTED_COMPLETED:
        return _match(EventType.CPR_STARTED, {}, ObservationKind.COMPLETED_ACTION, 0.92)
    if normalized in _CPR_RESUMED_COMMANDS:
        return _match(EventType.CPR_RESUMED, {}, ObservationKind.COMPLETED_ACTION, 0.9)
    if normalized in _CPR_RESUMED_COMPLETED:
        return _match(EventType.CPR_RESUMED, {}, ObservationKind.COMPLETED_ACTION, 0.92)
    if normalized in _CPR_PAUSED_COMMANDS:
        return _match(EventType.CPR_PAUSED, {}, ObservationKind.COMPLETED_ACTION, 0.88)
    if normalized in _RHYTHM_VF:
        return _match(
            EventType.RHYTHM_CHECKED,
            {"rhythm": "vf"},
            ObservationKind.RHYTHM_IDENTIFICATION,
            0.91,
        )
    if normalized in _RHYTHM_PVT:
        return _match(
            EventType.RHYTHM_CHECKED,
            {"rhythm": "pulseless_vt"},
            ObservationKind.RHYTHM_IDENTIFICATION,
            0.91,
        )
    if normalized in _RHYTHM_PEA:
        return _match(
            EventType.RHYTHM_CHECKED,
            {"rhythm": "pea"},
            ObservationKind.RHYTHM_IDENTIFICATION,
            0.91,
        )
    if normalized in _RHYTHM_ASYSTOLE:
        return _match(
            EventType.RHYTHM_CHECKED,
            {"rhythm": "asystole"},
            ObservationKind.RHYTHM_IDENTIFICATION,
            0.9,
        )
    if normalized in _ROSC_COMPLETED:
        return _match(
            EventType.ROSC_ACHIEVED,
            {"rhythm": "rosc"},
            ObservationKind.COMPLETED_ACTION,
            0.9,
        )
    if normalized in _ROSC_OBSERVATIONS:
        return _match(
            EventType.ROSC_ACHIEVED,
            {"rhythm": "rosc"},
            ObservationKind.OBSERVATION,
            0.87,
        )
    if normalized in _NO_SHOCK_CORRECTIONS:
        return _match(
            EventType.SHOCK_DELIVERED,
            {"is_positive": False},
            ObservationKind.CORRECTION,
            0.88,
        )
    if normalized in _NO_PULSE_NEGATIONS:
        return _match(
            EventType.ROSC_ACHIEVED,
            {"rhythm": "rosc", "is_positive": False},
            ObservationKind.CORRECTION,
            0.86,
        )
    return None


def _medication_phrase_match(
    normalized: str,
) -> tuple[EventType, dict[str, Any], ObservationKind, float] | None:
    medication = _medication_from_text(normalized)
    if medication is None:
        return None

    if _is_medication_negation(normalized):
        return _match(
            EventType.MEDICATION_GIVEN,
            {"medication": medication, "is_positive": False},
            ObservationKind.CORRECTION,
            0.84,
        )

    observation_kind = _medication_observation_kind(normalized)
    if observation_kind is None:
        return None

    payload: dict[str, Any] = {"medication": medication}
    dose = _dose_for_medication(medication, normalized)
    if dose is not None:
        payload.update({"dose": dose, "unit": "mg"})
    route = _route_from_text(normalized)
    if route is not None:
        payload["route"] = route

    return _match(
        EventType.MEDICATION_GIVEN,
        payload,
        observation_kind,
        _medication_extraction_confidence(observation_kind),
    )


def _medication_from_text(normalized: str) -> str | None:
    tokens = set(normalized.split())
    if tokens.intersection({"epi", "epinephrine", "adrenaline", "ادرينالين", "الادرينالين", "ابي"}):
        return "epinephrine"
    if tokens.intersection({"amio", "amiodarone", "اميو"}):
        return "amiodarone"
    if tokens.intersection({"lidocaine", "lido", "ليدوكايين"}):
        return "lidocaine"
    return None


def _medication_observation_kind(normalized: str) -> ObservationKind | None:
    if _contains_any(normalized, _MEDICATION_COMPLETED_MARKERS):
        return ObservationKind.COMPLETED_ACTION
    if _contains_any(normalized, _MEDICATION_COMMAND_MARKERS):
        return ObservationKind.COMMAND
    if _contains_dose_token(normalized):
        return ObservationKind.OBSERVATION
    return None


def _dose_for_medication(medication: str, normalized: str) -> float | None:
    if medication == "epinephrine" and _has_epinephrine_one_mg(normalized):
        return 1
    if medication == "amiodarone" and _has_numeric_dose(normalized, 300):
        return 300
    if medication == "lidocaine" and _has_numeric_dose(normalized, 100):
        return 100
    return None


def _route_from_text(normalized: str) -> str | None:
    tokens = set(normalized.split())
    has_iv = (
        "iv" in tokens
        or "وريدي" in tokens
        or "عن طريق الوريد" in normalized
    )
    has_io = "io" in tokens or "through the io" in normalized
    if has_iv == has_io:
        return None
    return "IV" if has_iv else "IO"


def _medication_extraction_confidence(observation_kind: ObservationKind) -> float:
    if observation_kind == ObservationKind.COMPLETED_ACTION:
        return 0.93
    if observation_kind == ObservationKind.COMMAND:
        return 0.9
    return 0.86


def _is_medication_negation(normalized: str) -> bool:
    if set(normalized.split()).intersection(_MEDICATION_NEGATION_MARKERS):
        return True
    return normalized in _MEDICATION_NEGATIONS


def _has_epinephrine_one_mg(normalized: str) -> bool:
    return _has_numeric_dose(normalized, 1) or _has_word_dose(normalized, "one") or _has_word_dose(normalized, "واحد")


def _has_numeric_dose(normalized: str, dose: int) -> bool:
    return bool(re.search(rf"(?<!\d){dose}(?:\.0)?(?!\d)", normalized))


def _has_word_dose(normalized: str, word: str) -> bool:
    return word in set(normalized.split())


def _contains_dose_token(normalized: str) -> bool:
    return bool(re.search(r"(?<!\d)(?:1|100|300)(?:\.0)?(?!\d)", normalized)) or bool(
        {"one", "واحد"}.intersection(normalized.split())
    )


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _match(
    event_type: EventType,
    payload: dict[str, Any],
    observation_kind: ObservationKind,
    confidence: float,
) -> tuple[EventType, dict[str, Any], ObservationKind, float]:
    return event_type, payload, observation_kind, confidence


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold()
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    text = text.replace("ى", "ي").replace("ة", "ه")
    text = text.translate(str.maketrans({char: " " for char in ".,!?،؛؟"}))
    return " ".join(text.replace("-", " ").replace("_", " ").split())


def _scaled_confidence(segment_confidence: float, extraction_confidence: float) -> float:
    return round(max(0.0, min(1.0, segment_confidence * extraction_confidence)), 4)


_EPINEPHRINE_COMMANDS = {
    "give epi",
    "give adrenaline",
    "give epinephrine",
    "ادي ادرينالين",
    "ادي ابي",
}
_EPINEPHRINE_COMPLETED = {
    "epi is in",
    "adrenaline 1 mg given",
    "adrenaline given",
    "epinephrine given",
    "ادرينالين اتدي",
    "الادرينالين دخل",
    "ادينا ادرينالين",
}
_AMIODARONE_COMMANDS = {"ادي اميو"}
_AMIODARONE_COMPLETED = {"amio given", "amiodarone given"}
_LIDOCAINE_COMPLETED = {"lidocaine given", "lido is in"}
_MEDICATION_COMMAND_MARKERS = ("give", "ادي")
_MEDICATION_COMPLETED_MARKERS = ("given", "is in", "دخل", "اتدي", "ادينا")
_MEDICATION_NEGATION_MARKERS = {"not", "مش", "متداش", "متديش"}

_SHOCK_COMPLETED = {
    "shock delivered",
    "shocked",
    "defibrillated",
    "shock اتعمل",
    "اتعمل shock",
    "صدمه اتعملت",
}
_SHOCK_COMMANDS = {"اشحن", "shock now"}
_SHOCK_PREPARATION_INTENTS = {"charging", "charged"}

_CPR_STARTED_COMMANDS = {"start cpr", "ابدا cpr"}
_CPR_STARTED_COMPLETED = {"cpr started"}
_CPR_RESUMED_COMMANDS = {
    "resume cpr",
    "resume compressions",
    "continue cpr",
    "continue cpr for 2 minutes",
    "continue compressions",
    "continue the cpr",
    "back on the chest",
    "compressions back on",
    "carry on cpr",
    "go back on cpr",
    "ارجع cpr",
    "ارجع ضغط",
    "كمل ضغط",
}
_CPR_RESUMED_COMPLETED = {"compressions resumed"}
_CPR_PAUSED_COMMANDS = {"وقف cpr", "pause compressions"}

_RHYTHM_VF = {
    "vf",
    "v fib",
    "ventricular fibrillation",
    "rhythm is vf",
    "vf detected",
    "patient still in vf after shocks",
    "في vf",
    "الريذم vf",
}
_RHYTHM_PVT = {"pvt", "pulseless vt"}
_RHYTHM_PEA = {"pea", "في pea", "الريذم pea"}
_RHYTHM_ASYSTOLE = {"asystole", "flatline", "اسستولي", "اسيستول"}

_ROSC_COMPLETED = {
    "rosc achieved",
    "return of spontaneous circulation",
    "pulse is back",
    "النبض رجع",
    "rosc حصل",
}
_ROSC_OBSERVATIONS = {"rosc", "we have a pulse", "في نبض", "في pulse"}

_NO_SHOCK_CORRECTIONS = {"no shock", "ما اتعملش shock", "shock ما اتعملش"}
_MEDICATION_NEGATIONS = {"not adrenaline", "مش ادرينالين"}
_NO_PULSE_NEGATIONS = {"no pulse", "مفيش نبض"}
