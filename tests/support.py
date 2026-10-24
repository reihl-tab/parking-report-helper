"""Shared helpers for the functional tests: run the real CLI, shared synthetic reporter data."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SAMPLE = ROOT / "sample.json"

REPORTER = {
    "first_name": "דנה",
    "last_name": "לוי",
    "id_type": "תעודת זהות",
    "id_number": "000000018",
    "phone": "0500000000",
    "email": "dana@example.com",
}


def run_cli(*args: str) -> subprocess.CompletedProcess:
    """Run `python -m parking_report` as a user would, in a separate process."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    src = str(ROOT / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "parking_report", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        env=env,
    )
