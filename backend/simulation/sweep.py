"""Run every scripted resuscitation scenario through the real Pulse pipeline.

    uv run --project backend python -m backend.simulation.sweep

Each scenario gets a fresh session and is fed as speech, one phrase at a time,
through the same code path the live microphone uses. Nothing is stubbed: the
normalizer, the evidence and fusion layer, the confirmation policy, and the
deterministic clinical engine all run for real.

Exit status is non-zero if any scenario fails, so this can gate a release.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

from backend.services.demo_workflow import (
    DemoTranscriptRequest,
    DemoWorkflowSession,
)
from backend.simulation.scenarios import ALL_SCENARIOS, Scenario


def run_scenario(scenario: Scenario) -> list[str]:
    """Feed one scenario through a fresh session. Returns a list of failures."""
    session = DemoWorkflowSession()

    # Phrases carry explicit timestamps rather than arriving in real time, so a
    # ten-minute code is swept in milliseconds and the run is reproducible.
    # The spacing is real: deduplication still sees the gaps it would see live.
    start = datetime(2026, 7, 21, 9, 0, 0, tzinfo=UTC)
    for index, phrase in enumerate(scenario.phrases):
        session.process_transcript(
            DemoTranscriptRequest(
                text=phrase,
                confidence=0.95,
                timestamp=start + timedelta(seconds=index * scenario.gap_seconds),
            )
        )

    state = session.current_state()
    failures: list[str] = []

    def check(label: str, actual: object, expected: object) -> None:
        if actual != expected:
            failures.append(f"{label}: expected {expected!r}, got {actual!r}")

    expect = scenario.expect
    if "rhythm" in expect:
        check("rhythm", str(state.current_rhythm), expect["rhythm"])
    if "cpr" in expect:
        check("cpr", str(state.cpr_status), expect["cpr"])
    if "shocks" in expect:
        check("shocks", state.shock_count, expect["shocks"])
    if "rosc" in expect:
        check("rosc", str(state.rosc_status), expect["rosc"])
    if "medications" in expect:
        joined = " ".join(str(m) for m in state.medication_history).lower()
        if str(expect["medications"]).lower() not in joined:
            failures.append(f"medications: {expect['medications']!r} not in {joined!r}")
    if "medications_absent" in expect:
        joined = " ".join(str(m) for m in state.medication_history).lower()
        if str(expect["medications_absent"]).lower() in joined:
            failures.append(
                f"medications: {expect['medications_absent']!r} should NOT be recorded"
            )
    if expect.get("safety_flagged"):
        if not state.safety_flags:
            failures.append("safety_flags: expected a deviation advisory, got none")

    return failures


def main(argv: list[str] | None = None) -> int:
    del argv
    print(f"Sweeping {len(ALL_SCENARIOS)} resuscitation scenarios\n")

    failed: list[tuple[Scenario, list[str]]] = []
    for index, scenario in enumerate(ALL_SCENARIOS, start=1):
        failures = run_scenario(scenario)
        if failures:
            failed.append((scenario, failures))
            print(f"  {index:2}. {scenario.name:<34} FAIL")
            for failure in failures:
                print(f"      {failure}")
        else:
            print(f"  {index:2}. {scenario.name:<34} pass")

    print()
    if failed:
        print(f"{len(failed)} of {len(ALL_SCENARIOS)} scenarios FAILED")
        for scenario, _ in failed:
            print(f"  - {scenario.name}: {scenario.why}")
        return 1

    print(f"All {len(ALL_SCENARIOS)} scenarios passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
