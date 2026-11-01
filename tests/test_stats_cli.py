"""End-to-end CLI tests for `p106-stats build` on synthetic exports (#210)."""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from parking_report.stats_cli import run

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "stats"


class BuildCommandTest(unittest.TestCase):
    def test_build_writes_summary_and_csvs(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            code = run(
                [
                    "build",
                    str(FIXTURES / "export_a.json"),
                    str(FIXTURES / "export_b.json"),
                    "-o",
                    str(out),
                ]
            )
            self.assertEqual(code, 0)
            summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["total_incidents"], 13)
            self.assertEqual(summary["duplicates_skipped"], 1)
            self.assertEqual(summary["schema_version"], 1)
            self.assertEqual(summary["inputs"], ["export_a.json", "export_b.json"])
            for name in ("monthly.csv", "categories.csv", "streets.csv", "report.html"):
                self.assertTrue((out / name).exists(), name)
            with (out / "monthly.csv").open(encoding="utf-8") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(rows[0], ["month", "reports"])
            self.assertIn(["2026-05", "4"], rows)

    def test_missing_input_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = run(
                ["build", str(FIXTURES / "missing.json"), "-o", str(Path(tmp) / "o")]
            )
            self.assertEqual(code, 1)

    def test_bad_top_streets_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = run(
                [
                    "build",
                    str(FIXTURES / "export_a.json"),
                    "-o",
                    str(Path(tmp) / "o"),
                    "--top-streets",
                    "0",
                ]
            )
            self.assertEqual(code, 2)


class PrivacyTest(unittest.TestCase):
    """Private source fields (plates, names in `description`) never leak (#210)."""

    PLATE = "12-345-67"
    PLATE_2 = "98-765-43"
    NAME = "ישראל ישראלי"
    NAME_2 = "משה כהן"

    def test_private_fields_absent_from_every_output_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            code = run(["build", str(FIXTURES / "export_private.json"), "-o", str(out)])
            self.assertEqual(code, 0)
            outputs = sorted(p.name for p in out.iterdir() if p.is_file())
            self.assertEqual(
                outputs,
                [
                    "categories.csv",
                    "monthly.csv",
                    "report.html",
                    "streets.csv",
                    "summary.json",
                ],
            )
            for path in out.iterdir():
                text = path.read_text(encoding="utf-8")
                for secret in (
                    self.PLATE,
                    self.PLATE_2,
                    self.NAME,
                    self.NAME_2,
                    "description",
                ):
                    self.assertNotIn(
                        secret, text, f"{secret!r} leaked into {path.name}"
                    )


if __name__ == "__main__":
    unittest.main()
