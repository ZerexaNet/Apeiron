"""End-to-end GUI verification.

The Qt interface is exercised by ``tools/gui_smoke.py`` in its own process:
Qt teardown inside a shared pytest process is not deterministic (destroying an
engine re-evaluates bindings against removed context properties), which made
in-process GUI tests flaky.  The subprocess reports a JSON result that is
asserted here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "tools" / "gui_smoke.py"


@pytest.mark.gui
def test_headless_gui_smoke(tmp_path: Path):
    if not SMOKE.exists():
        pytest.skip("gui_smoke.py is missing")
    pytest.importorskip("PySide6")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.pop("QT_QUICK_BACKEND", None)
    result = subprocess.run(
        [sys.executable, str(SMOKE), str(tmp_path / "gui-root")],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
        check=False,
    )
    payload: dict = {}
    start = result.stdout.find("{")
    if start >= 0:
        payload = json.loads(result.stdout[start:])
    assert payload.get("ok") is True, (
        f"gui smoke failed (exit {result.returncode})\n"
        f"stdout: {result.stdout[-2000:]}\nstderr: {result.stderr[-2000:]}"
    )
    names = {check["name"] for check in payload["checks"]}
    assert "page.AboutPage" in names
    assert "i18n.default_zh" in names
    assert "i18n.switch_en" in names
    assert "qt.no_warnings" in names
