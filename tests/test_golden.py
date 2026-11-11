"""Golden trace: the ideal scenario's Scope output, frozen in tests/golden/ideal.json.

Any change to timing behaviour shows up here as a failure, then as a reviewable git diff.
To regenerate on purpose (PowerShell):  $env:QTWIN_REGEN_GOLDEN=1; pytest tests/test_golden.py
That rewrites the file and still fails, so a regeneration can never pass silently.
Then clear the variable (Remove-Item Env:QTWIN_REGEN_GOLDEN) and review `git diff tests/golden`.
"""

import dataclasses
import json
import os
from pathlib import Path

import pytest

from qtwin.scenarios import ideal_commands, run_scenario

GOLDEN = Path(__file__).parent / "golden" / "ideal.json"


def test_ideal_trace_matches_golden() -> None:
    actual = [dataclasses.asdict(e) for e in run_scenario(ideal_commands())]
    if os.environ.get("QTWIN_REGEN_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(actual, indent=1) + "\n", encoding="utf-8")
        pytest.fail(f"regenerated {GOLDEN.name}; review the git diff, then rerun without the flag")
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert actual == expected
