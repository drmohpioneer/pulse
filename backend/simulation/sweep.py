"""Run the scripted resuscitation scenarios through the real Pulse pipeline.

Two ways to run, for two different questions.

**Did anything break?** Instant, deterministic, used as a release gate and by
pytest. Phrases carry explicit timestamps, so a ten-minute code is checked in
milliseconds while deduplication still sees the gaps it would see live.

    uv run --project backend python -m backend.simulation.sweep

**What does it actually look like?** Real time. Phrases are spoken at the pace
a real team speaks them, and with `--app` they are pushed into a running Pulse
so you can watch the dashboard move on screen while it happens.

    uv run --project backend python -m backend.simulation.sweep --live --app
    uv run --project backend python -m backend.simulation.sweep --live --speed 4
    uv run --project backend python -m backend.simulation.sweep --live --scenario hands_free_full_code

Nothing is stubbed in either mode: the normalizer, the evidence and fusion
layer, the confirmation policy, and the deterministic clinical engine all run
for real. Exit status is non-zero if any scenario fails.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

from backend.services.demo_workflow import (
    DemoTranscriptRequest,
    DemoWorkflowSession,
)
from backend.simulation.scenarios import ALL_SCENARIOS, Scenario

DEFAULT_APP_URL = "http://127.0.0.1:8000"


class _RemoteSession:
    """Drives a running Pulse over its HTTP API, so the dashboard reacts live."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def _post(self, path: str, payload: dict) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode())

    def reset(self) -> None:
        self._post("/api/demo/reset", {})

    def say(self, phrase: str) -> None:
        self._post("/api/demo/transcripts", {"text": phrase, "confidence": 0.95})

    def current_state(self):  # matches DemoWorkflowSession.current_state()
        from backend.services.demo_workflow import DemoStateResponse

        with urllib.request.urlopen(f"{self.base_url}/api/demo", timeout=20) as response:
            return DemoStateResponse.model_validate_json(response.read().decode())


def check_state(scenario: Scenario, state) -> list[str]:
    """Compare the engine's state against what the scenario says must be true."""
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
    if expect.get("safety_flagged") and not state.safety_flags:
        failures.append("safety_flags: expected a deviation advisory, got none")

    return failures


def run_scenario(scenario: Scenario) -> list[str]:
    """Instant, deterministic run. Timestamps are simulated, nothing sleeps."""
    session = DemoWorkflowSession()
    start = datetime(2026, 7, 21, 9, 0, 0, tzinfo=UTC)
    for index, phrase in enumerate(scenario.phrases):
        session.process_transcript(
            DemoTranscriptRequest(
                text=phrase,
                confidence=0.95,
                timestamp=start + timedelta(seconds=index * scenario.gap_seconds),
            )
        )
    return check_state(scenario, session.current_state())


def run_scenario_live(
    scenario: Scenario, *, speed: float = 1.0, app_url: str | None = None
) -> list[str]:
    """Real-time run: phrases land at the pace a team actually speaks them."""
    session = _RemoteSession(app_url) if app_url else DemoWorkflowSession()
    if isinstance(session, _RemoteSession):
        session.reset()

    gap = scenario.gap_seconds / speed
    began = time.monotonic()

    for index, phrase in enumerate(scenario.phrases):
        if index:
            time.sleep(gap)
        elapsed = time.monotonic() - began
        if isinstance(session, _RemoteSession):
            session.say(phrase)
        else:
            session.process_transcript(
                DemoTranscriptRequest(text=phrase, confidence=0.95)
            )
        state = session.current_state()
        print(
            f"      {elapsed:5.1f}s  “{phrase}”".ljust(52)
            + f"rhythm={state.current_rhythm:<9} shocks={state.shock_count} "
            f"cpr={state.cpr_status}"
        )

    return check_state(scenario, session.current_state())


def estimated_seconds(scenarios: tuple[Scenario, ...], speed: float) -> float:
    return sum(
        (len(scenario.phrases) - 1) * scenario.gap_seconds / speed
        for scenario in scenarios
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="backend.simulation.sweep",
        description="Run Pulse's scripted resuscitation scenarios.",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="run in real time, at the pace a team speaks, instead of instantly",
    )
    parser.add_argument(
        "--speed", type=float, default=1.0,
        help="with --live, play back this many times faster (default 1.0)",
    )
    parser.add_argument(
        "--app", nargs="?", const=DEFAULT_APP_URL, default=None, metavar="URL",
        help=f"with --live, drive a running Pulse so the dashboard moves on "
             f"screen (default {DEFAULT_APP_URL})",
    )
    parser.add_argument(
        "--scenario", action="append", metavar="NAME",
        help="run only this scenario; repeatable",
    )
    parser.add_argument(
        "--list", action="store_true", help="list the scenarios and exit",
    )
    args = parser.parse_args(argv)

    if args.list:
        for index, scenario in enumerate(ALL_SCENARIOS, start=1):
            print(f"{index:2}. {scenario.name:<34} {scenario.why}")
        return 0

    scenarios = ALL_SCENARIOS
    if args.scenario:
        wanted = set(args.scenario)
        scenarios = tuple(s for s in ALL_SCENARIOS if s.name in wanted)
        unknown = wanted - {s.name for s in ALL_SCENARIOS}
        if unknown:
            parser.error(f"unknown scenario(s): {', '.join(sorted(unknown))}")

    if args.speed <= 0:
        parser.error("--speed must be greater than zero")
    if args.app and not args.live:
        parser.error("--app only applies to --live runs")

    if args.live:
        minutes = estimated_seconds(scenarios, args.speed) / 60
        target = f", driving {args.app}" if args.app else ""
        print(f"Playing {len(scenarios)} scenario(s) in real time at "
              f"{args.speed:g}x{target}. About {minutes:.0f} min.")
        if args.speed > 1:
            print(
                "  Note: speeding playback up compresses the gaps between\n"
                "  phrases, so repeated events can fall inside a deduplication\n"
                "  window and be rejected, exactly as they would be if a real\n"
                "  team said them that fast. Scenarios asserting a second shock\n"
                "  are only meaningful at 1x."
            )
        print()
        if args.app:
            try:
                _RemoteSession(args.app).current_state()
            except (urllib.error.URLError, OSError) as exc:
                print(f"Cannot reach Pulse at {args.app}: {exc}\n"
                      f"Start it with: uv run --project backend uvicorn "
                      f"backend.api.main:app --port 8000")
                return 2
    else:
        print(f"Sweeping {len(scenarios)} resuscitation scenarios\n")

    failed: list[tuple[Scenario, list[str]]] = []
    for index, scenario in enumerate(scenarios, start=1):
        if args.live:
            print(f"  {index:2}. {scenario.name} — {scenario.why}")
            failures = run_scenario_live(scenario, speed=args.speed, app_url=args.app)
        else:
            failures = run_scenario(scenario)

        if failures:
            failed.append((scenario, failures))
            print(f"  {index:2}. {scenario.name:<34} FAIL")
            for failure in failures:
                print(f"      {failure}")
        elif not args.live:
            print(f"  {index:2}. {scenario.name:<34} pass")
        else:
            print(f"      passed\n")

    print()
    if failed:
        print(f"{len(failed)} of {len(scenarios)} scenarios FAILED")
        for scenario, _ in failed:
            print(f"  - {scenario.name}: {scenario.why}")
        return 1

    print(f"All {len(scenarios)} scenarios passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
