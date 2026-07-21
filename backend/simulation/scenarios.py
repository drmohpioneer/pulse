"""The 29 scripted resuscitation scenarios Pulse is swept against.

Each scenario is a list of phrases exactly as a team says them out loud, plus
the state the deterministic engine must be in once the phrases have been fed.

These are deliberately written as *speech*, not as API calls. They enter Pulse
through the same normalization, evidence, fusion, and confirmation path the
live microphone uses, so a scenario failing here means a real room would fail
the same way.

`expect` keys map to fields on the clinical state:

    rhythm, cpr, shocks, rosc, medications  (medications = substring match)

A `None` value means "the scenario asserts nothing about this field".
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Scenario:
    name: str
    why: str
    phrases: tuple[str, ...]
    expect: dict[str, object] = field(default_factory=dict)
    # Virtual seconds between phrases. The default spaces events like a real
    # code, so repeat shocks fall outside the deduplication window. Echo
    # scenarios lower it deliberately to fire inside that window.
    gap_seconds: float = 20.0


# --------------------------------------------------------------------------
# 1-6  Classic shockable arrests
# --------------------------------------------------------------------------
CLASSIC = [
    Scenario(
        "vf_single_shock",
        "The simplest shockable arrest: recognise VF, shock it, resume CPR.",
        ("cpr started", "rhythm is vf", "shock delivered", "resume compressions"),
        {"rhythm": "VF", "cpr": "Active", "shocks": 1},
    ),
    Scenario(
        "vf_two_cycles",
        "A second shock after a repeat rhythm check must count separately.",
        ("cpr started", "rhythm is vf", "shock delivered", "resume compressions",
         "rhythm is still vf", "shock delivered"),
        {"shocks": 2, "rhythm": "VF"},
    ),
    Scenario(
        "pvt_is_shockable",
        "Pulseless VT follows the same shockable pathway as VF.",
        ("cpr started", "pulseless vt", "shock delivered"),
        {"shocks": 1},
    ),
    Scenario(
        "vf_with_epinephrine",
        "An order plus its spoken completion closes the loop on a drug.",
        ("cpr started", "rhythm is vf", "shock delivered", "resume compressions",
         "give 1 mg epinephrine", "epi is in"),
        {"shocks": 1, "medications": "epinephrine"},
    ),
    Scenario(
        "vf_to_rosc",
        "A stated ROSC ends the arrest.",
        ("cpr started", "rhythm is vf", "shock delivered", "pulse is back"),
        {"rosc": "Achieved"},
    ),
    Scenario(
        "vf_three_shocks_amiodarone",
        "After the third shock the engine should raise an antiarrhythmic.",
        ("cpr started", "rhythm is vf", "shock delivered", "resume compressions",
         "rhythm is vf", "shock delivered", "resume compressions",
         "rhythm is vf", "shock delivered"),
        {"shocks": 3},
    ),
]

# --------------------------------------------------------------------------
# 7-12  Non-shockable arrests
# --------------------------------------------------------------------------
NON_SHOCKABLE = [
    Scenario(
        "asystole_no_shock",
        "Asystole is not shockable; nothing should push a shock.",
        ("cpr started", "asystole", "resume compressions"),
        {"rhythm": "Asystole", "shocks": 0},
    ),
    Scenario(
        "pea_arrest",
        "PEA follows the non-shockable pathway.",
        ("cpr started", "pea", "resume compressions"),
        {"rhythm": "PEA", "shocks": 0},
    ),
    Scenario(
        "asystole_with_epinephrine",
        "Epinephrine is the priority drug in a non-shockable arrest.",
        ("cpr started", "asystole", "give 1 mg epinephrine", "epi is in"),
        {"shocks": 0, "medications": "epinephrine"},
    ),
    Scenario(
        "pea_to_rosc",
        "ROSC can follow a non-shockable arrest.",
        ("cpr started", "pea", "give 1 mg epinephrine", "epi is in", "pulse is back"),
        {"rosc": "Achieved"},
    ),
    Scenario(
        "asystole_prolonged",
        "A long asystolic arrest still never accrues a shock.",
        ("cpr started", "asystole", "resume compressions", "asystole",
         "resume compressions", "asystole"),
        {"shocks": 0, "rhythm": "Asystole"},
    ),
    Scenario(
        "flatline_wording",
        "'Flatline' is what people actually say instead of 'asystole'.",
        ("cpr started", "flatline"),
        {"rhythm": "Asystole"},
    ),
]

# --------------------------------------------------------------------------
# 13-18  Rhythm changes mid-code
# --------------------------------------------------------------------------
INTERTANGLED = [
    Scenario(
        "vf_to_asystole",
        "A shockable arrest degenerating into asystole.",
        ("cpr started", "rhythm is vf", "shock delivered", "resume compressions",
         "asystole"),
        {"rhythm": "Asystole", "shocks": 1},
    ),
    Scenario(
        "asystole_to_vf",
        "A non-shockable arrest becoming shockable must open the shock pathway.",
        ("cpr started", "asystole", "resume compressions", "rhythm is vf"),
        {"rhythm": "VF"},
    ),
    Scenario(
        "vf_asystole_vf",
        "Rhythm flipping twice must not confuse the shock count.",
        ("cpr started", "rhythm is vf", "shock delivered", "asystole",
         "resume compressions", "rhythm is vf", "shock delivered"),
        {"shocks": 2, "rhythm": "VF"},
    ),
    Scenario(
        "pea_to_vf",
        "PEA converting to VF.",
        ("cpr started", "pea", "resume compressions", "rhythm is vf",
         "shock delivered"),
        {"shocks": 1, "rhythm": "VF"},
    ),
    Scenario(
        "rosc_then_rearrest",
        "Re-arrest after ROSC must reopen the arrest, not stay in ROSC.",
        ("cpr started", "rhythm is vf", "shock delivered", "pulse is back",
         "rhythm is vf"),
        {"rhythm": "VF"},
    ),
    Scenario(
        "rosc_then_rearrest_shock",
        "A shock after re-arrest must still be counted.",
        ("cpr started", "rhythm is vf", "shock delivered", "pulse is back",
         "rhythm is vf", "shock delivered"),
        {"shocks": 2},
    ),
]

# --------------------------------------------------------------------------
# 19-24  Safety: echoes, negations, corrections, off-protocol events
# --------------------------------------------------------------------------
SAFETY = [
    Scenario(
        "echo_duplicate_shock",
        "The same shock shouted by three people is still one shock.",
        ("cpr started", "rhythm is vf", "shock delivered", "shock delivered",
         "shock delivered"),
        {"shocks": 1},
        gap_seconds=2.0,
    ),
    Scenario(
        "echo_duplicate_five",
        "Five echoes of one shock must not inflate the count.",
        ("cpr started", "rhythm is vf", "shock delivered", "shock delivered",
         "shock delivered", "shock delivered", "shock delivered"),
        {"shocks": 1},
        gap_seconds=2.0,
    ),
    Scenario(
        "negation_no_shock",
        "A denial must never create a positive event.",
        ("cpr started", "rhythm is vf", "no shock"),
        {"shocks": 0},
    ),
    Scenario(
        "negation_no_pulse",
        "'No pulse' must not be read as ROSC.",
        ("cpr started", "rhythm is vf", "no pulse"),
        {"rosc": "Unknown"},
    ),
    Scenario(
        "off_protocol_shock_in_asystole",
        "A shock during asystole is accepted but must be flagged as a deviation.",
        ("cpr started", "asystole", "shock delivered"),
        {"safety_flagged": True},
    ),
    Scenario(
        "order_without_completion_is_not_given",
        "An order alone must not record a drug as given.",
        ("cpr started", "rhythm is vf", "give 1 mg epinephrine"),
        {"medications_absent": "epinephrine"},
    ),
]

# --------------------------------------------------------------------------
# 25-29  Real-room speech: chatter, code-switching, natural phrasing
# --------------------------------------------------------------------------
REAL_SPEECH = [
    Scenario(
        "room_chatter_is_ignored",
        "Ordinary talk in a loud room must not move clinical state.",
        ("cpr started", "can someone get the door", "who is documenting",
         "the family is outside", "hand me the gloves"),
        {"shocks": 0, "rhythm": "Unknown"},
    ),
    Scenario(
        "arabic_rhythm_codeswitch",
        "Egyptian clinicians say the rhythm name in English inside Arabic.",
        ("cpr started", "الريذم vf"),
        {"rhythm": "VF"},
    ),
    Scenario(
        "arabic_rosc",
        "ROSC stated in Arabic.",
        ("cpr started", "rhythm is vf", "shock delivered", "النبض رجع"),
        {"rosc": "Achieved"},
    ),
    Scenario(
        "arabic_negation",
        "A negation stated in Arabic is still a negation.",
        ("cpr started", "rhythm is vf", "مفيش نبض"),
        {"rosc": "Unknown"},
    ),
    Scenario(
        "hands_free_full_code",
        "A complete arrest run entirely by speech, nobody touching the screen.",
        ("cpr started", "rhythm is vf", "shock delivered", "resume compressions",
         "give 1 mg epinephrine", "epi is in", "rhythm is vf", "shock delivered",
         "resume compressions", "pulse is back"),
        {"rosc": "Achieved", "shocks": 2, "medications": "epinephrine"},
    ),
]

ALL_SCENARIOS: tuple[Scenario, ...] = tuple(
    CLASSIC + NON_SHOCKABLE + INTERTANGLED + SAFETY + REAL_SPEECH
)
