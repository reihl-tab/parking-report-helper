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


class PlatesLedgerTest(unittest.TestCase):
    """--plates-ledger adds an aggregated plates.csv (issue #220)."""

    def _write_ledger(self, tmp: Path) -> Path:
        path = Path(tmp) / "ledger.csv"
        path.write_text(
            "filed_date,plate,municipal_ref,event_type\n"
            "2026-10-01,1122233,2601-000001,חניה\n"
            "2026-10-02,11-222-33,2601-000002,חניה\n"
            "2026-10-03,1122233,2601-000003,חניה\n"
            "2026-10-04,9988776,2601-000004,חניה\n",
            encoding="utf-8",
        )
        return path

    def test_plates_csv_written_with_aggregate(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            code = run(
                [
                    "build",
                    str(FIXTURES / "export_a.json"),
                    "-o",
                    str(out),
                    "--plates-ledger",
                    str(self._write_ledger(Path(tmp))),
                ]
            )
            self.assertEqual(code, 0)
            with (out / "plates.csv").open(encoding="utf-8") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(
                rows,
                [
                    ["plate", "reports"],
                    ["11-222-33", "3"],
                ],
            )
            summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["repeat_plates_count"], 1)

    def test_without_ledger_no_plates_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            code = run(["build", str(FIXTURES / "export_a.json"), "-o", str(out)])
            self.assertEqual(code, 0)
            self.assertFalse((out / "plates.csv").exists())
            summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["repeat_plates_count"], 0)

    def test_missing_ledger_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = run(
                [
                    "build",
                    str(FIXTURES / "export_a.json"),
                    "-o",
                    str(Path(tmp) / "o"),
                    "--plates-ledger",
                    str(Path(tmp) / "missing.csv"),
                ]
            )
            self.assertEqual(code, 1)

    def test_ledger_path_itself_never_appears_in_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            code = run(
                [
                    "build",
                    str(FIXTURES / "export_a.json"),
                    "-o",
                    str(out),
                    "--plates-ledger",
                    str(self._write_ledger(Path(tmp))),
                ]
            )
            self.assertEqual(code, 0)
            for path in out.iterdir():
                self.assertNotIn(
                    "ledger.csv",
                    path.read_text(encoding="utf-8"),
                    f"ledger path leaked into {path.name}",
                )

    def test_stale_plates_csv_removed_when_none_qualify(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            out.mkdir()
            stale = out / "plates.csv"
            stale.write_text("plate,reports\n11-222-33,5\n", encoding="utf-8")
            ledger = Path(tmp) / "ledger.csv"
            ledger.write_text(
                "filed_date,plate,municipal_ref\n"
                "2026-10-01,1122233,2601-000001\n"
                "2026-10-02,1122233,2601-000002\n",
                encoding="utf-8",
            )
            code = run(
                [
                    "build",
                    str(FIXTURES / "export_a.json"),
                    "-o",
                    str(out),
                    "--plates-ledger",
                    str(ledger),
                ]
            )
            self.assertEqual(code, 0)
            self.assertFalse(stale.exists())
            summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["repeat_plates_count"], 0)

    def test_stale_plates_csv_removed_even_without_the_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            out.mkdir()
            stale = out / "plates.csv"
            stale.write_text("plate,reports\n11-222-33,5\n", encoding="utf-8")
            code = run(["build", str(FIXTURES / "export_a.json"), "-o", str(out)])
            self.assertEqual(code, 0)
            self.assertFalse(stale.exists())

    def test_excluded_rows_reported_on_stderr(self):
        import io
        from contextlib import redirect_stderr

        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.csv"
            ledger.write_text(
                "filed_date,plate,municipal_ref\n"
                "2026-10-01,not-a-plate,2601-000001\n"
                "2026-02-30,1122233,2601-000002\n",
                encoding="utf-8",
            )
            buf = io.StringIO()
            with redirect_stderr(buf):
                code = run(
                    [
                        "build",
                        str(FIXTURES / "export_a.json"),
                        "-o",
                        str(Path(tmp) / "o"),
                        "--plates-ledger",
                        str(ledger),
                    ]
                )
            self.assertEqual(code, 0)
            self.assertIn("2 row(s) excluded", buf.getvalue())

    def test_ledger_without_plate_column_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.csv"
            ledger.write_text(
                "filed_date,event_type\n2026-10-01,חניה\n", encoding="utf-8"
            )
            code = run(
                [
                    "build",
                    str(FIXTURES / "export_a.json"),
                    "-o",
                    str(Path(tmp) / "o"),
                    "--plates-ledger",
                    str(ledger),
                ]
            )
            self.assertEqual(code, 1)
            self.assertFalse((Path(tmp) / "o" / "plates.csv").exists())


if __name__ == "__main__":
    unittest.main()
