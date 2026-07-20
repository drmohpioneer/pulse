from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from backend.models.common import Explanation
from backend.workflow.base import BaseWorkflowStateMachine
from backend.workflow.events import ClinicalEvent, EventStatus, EventType
from backend.workflow.recommendations import Recommendation, RecommendationPriority
from backend.workflow.rhythm import RhythmCategory, RhythmName


class MedicationAdministration(BaseModel):
    model_config = ConfigDict(frozen=True)

    medication_name: str
    dose: float | None = None
    unit: str | None = None
    route: str | None = None
    administered_at: datetime
    event_id: UUID


class MedicationState(BaseModel):
    model_config = ConfigDict(frozen=True)

    administrations: tuple[MedicationAdministration, ...] = Field(default_factory=tuple)
    epinephrine_count: int = 0
    last_epinephrine_at: datetime | None = None
    last_epinephrine_event_id: UUID | None = None
    amiodarone_count: int = 0
    last_amiodarone_at: datetime | None = None
    last_amiodarone_event_id: UUID | None = None
    lidocaine_count: int = 0
    last_lidocaine_at: datetime | None = None
    last_lidocaine_event_id: UUID | None = None
    shock_count: int = 0
    latest_rhythm_name: RhythmName = RhythmName.UNKNOWN
    latest_rhythm_category: RhythmCategory = RhythmCategory.UNKNOWN
    rosc_achieved: bool = False
    epinephrine_eligible: bool = False
    epinephrine_due: bool = False
    amiodarone_eligible: bool = False
    lidocaine_eligible: bool = False


_ACCEPTED_STATUSES = {EventStatus.ACCEPTED, EventStatus.CORRECTED}
_EPINEPHRINE_INTERVAL = timedelta(minutes=4)
_RHYTHM_ALIASES: Mapping[str, RhythmName] = {
    "vf": RhythmName.VF,
    "ventricular_fibrillation": RhythmName.VF,
    "ventricular fibrillation": RhythmName.VF,
    "pvt": RhythmName.PULSELESS_VT,
    "pulseless_vt": RhythmName.PULSELESS_VT,
    "pulseless vt": RhythmName.PULSELESS_VT,
    "pulseless_ventricular_tachycardia": RhythmName.PULSELESS_VT,
    "pulseless ventricular tachycardia": RhythmName.PULSELESS_VT,
    "pea": RhythmName.PEA,
    "asystole": RhythmName.ASYSTOLE,
    "shockable": RhythmName.SHOCKABLE_UNKNOWN,
    "non_shockable": RhythmName.NON_SHOCKABLE_UNKNOWN,
    "nonshockable": RhythmName.NON_SHOCKABLE_UNKNOWN,
    "organized": RhythmName.ORGANIZED,
    "rosc": RhythmName.ROSC,
}
_MEDICATION_ALIASES: Mapping[str, str] = {
    "epi": "epinephrine",
    "epinephrine": "epinephrine",
    "adrenaline": "epinephrine",
    "amio": "amiodarone",
    "amiodarone": "amiodarone",
    "lido": "lidocaine",
    "lidocaine": "lidocaine",
}


class MedicationStateMachine(BaseWorkflowStateMachine[MedicationState]):
    """Deterministic medication state machine for shockable arrest timing."""

    def _initial_state(self) -> MedicationState:
        return MedicationState()

    def apply_event(self, event: ClinicalEvent) -> MedicationState:
        if event.status not in _ACCEPTED_STATUSES:
            return self.get_state()

        if event.event_type == EventType.RHYTHM_CHECKED:
            rhythm = self._rhythm_from_payload(event.payload)
            category = self._category_for(rhythm)
            self._state = self.get_state().model_copy(
                update={
                    "latest_rhythm_name": rhythm,
                    "latest_rhythm_category": category,
                    "rosc_achieved": category == RhythmCategory.ROSC,
                }
            )
            self._refresh_due_flags(event.timestamp)
            return self.get_state()

        if event.event_type == EventType.SHOCK_DELIVERED:
            state = self.get_state()
            self._state = state.model_copy(update={"shock_count": state.shock_count + 1})
            self._refresh_due_flags(event.timestamp)
            return self.get_state()

        if event.event_type == EventType.MEDICATION_GIVEN:
            administration = self._administration_from_event(event)
            state = self.get_state()
            update: dict[str, object] = {
                "administrations": (*state.administrations, administration),
            }
            if administration.medication_name == "epinephrine":
                update.update(
                    {
                        "epinephrine_count": state.epinephrine_count + 1,
                        "last_epinephrine_at": administration.administered_at,
                        "last_epinephrine_event_id": event.id,
                    }
                )
            elif administration.medication_name == "amiodarone":
                update.update(
                    {
                        "amiodarone_count": state.amiodarone_count + 1,
                        "last_amiodarone_at": administration.administered_at,
                        "last_amiodarone_event_id": event.id,
                    }
                )
            elif administration.medication_name == "lidocaine":
                update.update(
                    {
                        "lidocaine_count": state.lidocaine_count + 1,
                        "last_lidocaine_at": administration.administered_at,
                        "last_lidocaine_event_id": event.id,
                    }
                )
            self._state = state.model_copy(update=update)
            self._refresh_due_flags(event.timestamp)
            return self.get_state()

        if event.event_type == EventType.ROSC_ACHIEVED:
            self._state = self.get_state().model_copy(
                update={
                    "rosc_achieved": True,
                    "epinephrine_due": False,
                    "amiodarone_eligible": False,
                    "lidocaine_eligible": False,
                }
            )
            return self.get_state()

        return self.get_state()

    def get_recommendations(self, as_of: datetime | None = None) -> list[Recommendation]:
        state = self.get_state()
        flags = self._due_flags(state, as_of)
        if state.rosc_achieved:
            return []

        recommendations: list[Recommendation] = []
        if flags["epinephrine_due"]:
            recommendations.append(
                Recommendation(
                    id="medications.give_epinephrine",
                    priority=RecommendationPriority.HIGH,
                    message="Give epinephrine.",
                    rationale="Epinephrine is due when no accepted dose has been recorded in the last 4 minutes during active arrest.",
                    referenced_state_fields=[
                        "shock_count",
                        "epinephrine_count",
                        "last_epinephrine_at",
                        "epinephrine_due",
                    ],
                )
            )

        if flags["amiodarone_eligible"]:
            second_dose = state.amiodarone_count == 1
            recommendations.append(
                Recommendation(
                    id="medications.consider_amiodarone",
                    priority=RecommendationPriority.HIGH,
                    message=(
                        "Consider amiodarone 150 mg."
                        if second_dose
                        else "Consider amiodarone."
                    ),
                    rationale=(
                        "After the fifth shock in persistent VF/pVT, a second 150 mg amiodarone dose may be considered during the subsequent CPR cycle."
                        if second_dose
                        else "After the third shock in persistent VF/pVT, amiodarone may be considered during the subsequent CPR cycle."
                    ),
                    referenced_state_fields=[
                        "shock_count",
                        "amiodarone_count",
                        "amiodarone_eligible",
                    ],
                )
            )

        if flags["lidocaine_eligible"]:
            recommendations.append(
                Recommendation(
                    id="medications.consider_lidocaine",
                    priority=RecommendationPriority.HIGH,
                    message="Consider lidocaine.",
                    rationale="After the third shock in persistent VF/pVT, lidocaine may be considered as an alternative antiarrhythmic during the subsequent CPR cycle.",
                    referenced_state_fields=[
                        "shock_count",
                        "lidocaine_count",
                        "lidocaine_eligible",
                    ],
                )
            )

        return recommendations

    def explain(self) -> Explanation:
        state = self.get_state()
        return Explanation(
            summary=(
                f"{state.epinephrine_count} epinephrine and "
                f"{state.amiodarone_count} amiodarone and "
                f"{state.lidocaine_count} lidocaine administration(s) recorded."
            ),
            referenced_event_ids=[
                str(item.event_id) for item in state.administrations
            ],
            referenced_state_fields=[
                "administrations",
                "shock_count",
                "epinephrine_due",
                "amiodarone_eligible",
                "lidocaine_eligible",
            ],
            metadata=state.model_dump(mode="json"),
        )

    def replay(self, events: Iterable[ClinicalEvent]) -> MedicationState:
        ordered_events = tuple(events)
        superseded_event_ids = {
            event.supersedes_event_id
            for event in ordered_events
            if event.supersedes_event_id is not None
        }

        self.reset()
        for event in ordered_events:
            if event.id in superseded_event_ids:
                continue
            self.apply_event(event)
        return self.get_state()

    def _refresh_due_flags(self, as_of: datetime) -> None:
        state = self.get_state()
        flags = self._due_flags(state, as_of)
        self._state = state.model_copy(update=flags)

    @staticmethod
    def _due_flags(state: MedicationState, as_of: datetime | None) -> dict[str, bool]:
        shockable = state.latest_rhythm_category == RhythmCategory.SHOCKABLE
        active_arrest_context = state.latest_rhythm_category in {
            RhythmCategory.SHOCKABLE,
            RhythmCategory.NON_SHOCKABLE,
        }
        epinephrine_eligible = (
            active_arrest_context
            and not state.rosc_achieved
            and (
                state.latest_rhythm_category == RhythmCategory.NON_SHOCKABLE
                or state.shock_count >= 2
                or state.epinephrine_count > 0
            )
        )
        epinephrine_due = False
        if epinephrine_eligible and as_of is not None:
            epinephrine_due = (
                state.last_epinephrine_at is None
                or as_of >= state.last_epinephrine_at + _EPINEPHRINE_INTERVAL
            )

        amiodarone_eligible = (
            shockable
            and (
                (state.amiodarone_count == 0 and state.shock_count >= 3)
                or (state.amiodarone_count == 1 and state.shock_count >= 5)
            )
            and state.lidocaine_count == 0
            and not state.rosc_achieved
        )
        lidocaine_eligible = (
            shockable
            and state.shock_count >= 3
            and state.lidocaine_count == 0
            and state.amiodarone_count == 0
            and not state.rosc_achieved
        )

        return {
            "epinephrine_eligible": epinephrine_eligible,
            "epinephrine_due": epinephrine_due,
            "amiodarone_eligible": amiodarone_eligible,
            "lidocaine_eligible": lidocaine_eligible,
        }

    @staticmethod
    def _administration_from_event(event: ClinicalEvent) -> MedicationAdministration:
        raw_medication = event.payload.get("medication") or event.payload.get("medication_name")
        normalized_medication = MedicationStateMachine._normalize_medication(raw_medication)
        return MedicationAdministration(
            medication_name=normalized_medication,
            dose=MedicationStateMachine._optional_float(event.payload.get("dose")),
            unit=MedicationStateMachine._optional_str(event.payload.get("unit")),
            route=MedicationStateMachine._optional_str(event.payload.get("route")),
            administered_at=event.timestamp,
            event_id=event.id,
        )

    @staticmethod
    def _normalize_medication(value: object) -> str:
        if value is None:
            return "unknown"
        normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
        return _MEDICATION_ALIASES.get(normalized, normalized)

    @staticmethod
    def _optional_str(value: object) -> str | None:
        if value is None:
            return None
        return str(value)

    @staticmethod
    def _optional_float(value: object) -> float | None:
        if value is None:
            return None
        return float(value)

    @staticmethod
    def _rhythm_from_payload(payload: Mapping[str, Any]) -> RhythmName:
        raw_value = payload.get("rhythm") or payload.get("rhythm_name")
        if isinstance(raw_value, RhythmName):
            return raw_value
        if raw_value is None:
            if payload.get("shockable") is True:
                return RhythmName.SHOCKABLE_UNKNOWN
            if payload.get("shockable") is False:
                return RhythmName.NON_SHOCKABLE_UNKNOWN
            return RhythmName.UNKNOWN
        normalized = str(raw_value).strip().lower().replace("-", "_")
        return _RHYTHM_ALIASES.get(normalized, RhythmName.UNKNOWN)

    @staticmethod
    def _category_for(rhythm: RhythmName) -> RhythmCategory:
        if rhythm in {
            RhythmName.VF,
            RhythmName.PULSELESS_VT,
            RhythmName.SHOCKABLE_UNKNOWN,
        }:
            return RhythmCategory.SHOCKABLE
        if rhythm in {
            RhythmName.PEA,
            RhythmName.ASYSTOLE,
            RhythmName.NON_SHOCKABLE_UNKNOWN,
        }:
            return RhythmCategory.NON_SHOCKABLE
        if rhythm == RhythmName.ROSC:
            return RhythmCategory.ROSC
        if rhythm == RhythmName.ORGANIZED:
            return RhythmCategory.ORGANIZED
        return RhythmCategory.UNKNOWN
