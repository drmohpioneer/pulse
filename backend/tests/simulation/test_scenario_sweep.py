"""The scripted resuscitation sweep, run as part of the normal test suite.

`backend/simulation/sweep.py` is the same body of scenarios with human-readable
output for release checks. This file makes them a regression gate too, so a
change that breaks a clinical scenario fails CI rather than waiting to be
noticed during a demo.
"""

from __future__ import annotations

import pytest

from backend.simulation.scenarios import ALL_SCENARIOS, Scenario
from backend.simulation.sweep import run_scenario


def test_the_suite_covers_the_documented_number_of_scenarios() -> None:
    assert len(ALL_SCENARIOS) == 29


def test_scenario_names_are_unique() -> None:
    names = [scenario.name for scenario in ALL_SCENARIOS]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=lambda s: s.name)
def test_scenario(scenario: Scenario) -> None:
    failures = run_scenario(scenario)
    assert not failures, f"{scenario.name} ({scenario.why}): " + "; ".join(failures)
