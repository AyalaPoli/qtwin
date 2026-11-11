"""Hard rule 3: driver code sees the device only through the bus, never by importing qtwin.device.

The check is static (ast), so it catches violations even in code paths no test executes.
"""

import ast
from pathlib import Path

import pytest

DRIVER_DIR = Path(__file__).resolve().parents[1] / "src" / "qtwin" / "driver"


def find_device_imports(source: str) -> list[str]:
    """Return a description of every import of qtwin.device found in `source`."""
    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            # import qtwin.device / import qtwin.device.fifo as f
            for alias in node.names:
                if alias.name == "qtwin.device" or alias.name.startswith("qtwin.device."):
                    hits.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported = {alias.name for alias in node.names}
            if node.level == 0:
                # from qtwin.device[.x] import y   /   from qtwin import device
                if module == "qtwin.device" or module.startswith("qtwin.device."):
                    hits.append(f"line {node.lineno}: from {module} import ...")
                elif module == "qtwin" and "device" in imported:
                    hits.append(f"line {node.lineno}: from qtwin import device")
            elif node.level >= 2:
                # Relative imports from inside qtwin/driver/: level 1 stays in driver/,
                # level 2 reaches the qtwin package, where `device` lives.
                if module == "device" or module.startswith("device."):
                    hits.append(f"line {node.lineno}: relative import of {module}")
                elif module == "" and "device" in imported:
                    hits.append(f"line {node.lineno}: relative import of device")
    return hits


def test_driver_package_never_imports_device() -> None:
    files = sorted(DRIVER_DIR.rglob("*.py"))
    assert files, "driver/ package not found; the boundary check would be vacuous"
    violations = [
        f"{path.relative_to(DRIVER_DIR.parent)}: {hit}"
        for path in files
        for hit in find_device_imports(path.read_text(encoding="utf-8"))
    ]
    assert violations == []


# Prove the checker itself works: driver/ is nearly empty today, so the test above
# would pass even with a broken checker.
@pytest.mark.parametrize(
    "bad_source",
    [
        "import qtwin.device",
        "import qtwin.device.fifo as fifo",
        "from qtwin.device import registers",
        "from qtwin.device.sequencer import Sequencer",
        "from qtwin import device",
        "from qtwin import bus, device",
        "from ..device import fifo",
        "from ..device.clock import Clock",
        "from .. import device",
    ],
)
def test_checker_catches_device_imports(bad_source: str) -> None:
    assert find_device_imports(bad_source) != []


@pytest.mark.parametrize(
    "allowed_source",
    [
        "from qtwin import bus",
        "from qtwin.bus import Bus",
        "from qtwin.device_docs import x",  # name only starts with 'device'
        "from . import polling",  # level-1 relative import stays inside driver/
        "from .device import helper",  # a hypothetical driver/device.py, not qtwin.device
        "from .. import scope",
        "import qtwin.scope",
    ],
)
def test_checker_allows_other_imports(allowed_source: str) -> None:
    assert find_device_imports(allowed_source) == []
