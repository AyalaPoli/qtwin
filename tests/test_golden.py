"""Golden traces: each scenario's Scope output, frozen in tests/golden/<name>.json.

Any change to timing behaviour shows up here as a failure, then as a reviewable git diff.
A golden file detects change, not correctness: test_fault_scenarios.py holds hand-computed edges.
To regenerate on purpose (PowerShell):  $env:QTWIN_REGEN_GOLDEN=1; pytest tests/test_golden.py
That rewrites the files and still fails, so a regeneration can never pass silently.
Then clear the variable (Remove-Item Env:QTWIN_REGEN_GOLDEN) and review `git diff tests/golden`.
"""

import dataclasses
import json
import os
from pathlib import Path

import pytest

from qtwin.scenarios import SCENARIOS, run_scenario

GOLDEN_DIR = Path(__file__).parent / "golden"


@pytest.mark.parametrize("name", SCENARIOS)
def test_trace_matches_golden(name: str) -> None:
    scenario = SCENARIOS[name]
    actual = [dataclasses.asdict(e) for e in run_scenario(scenario.commands, scenario.timing)]
    golden = GOLDEN_DIR / f"{name}.json"
    if os.environ.get("QTWIN_REGEN_GOLDEN") == "1":
        GOLDEN_DIR.mkdir(exist_ok=True)
        golden.write_text(json.dumps(actual, indent=1) + "\n", encoding="utf-8")
        pytest.fail(f"regenerated {golden.name}; review the git diff, then rerun without the flag")
    expected = json.loads(golden.read_text(encoding="utf-8"))
    assert actual == expected
