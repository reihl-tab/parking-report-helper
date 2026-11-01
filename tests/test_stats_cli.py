"""End-to-end CLI tests for `p106-stats build` on synthetic exports (#210)."""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


class RefreshCommandTest(unittest.TestCase):
    """`p106-stats refresh`: mocked fetch + archived export union (#232)."""

    ENV = {
        "PARKING_106_SUBSCRIPTION_KEY": "synthetic-key",
        "PARKING_106_USERNAME": "synthetic-user",
        "PARKING_106_PASSWORD": "synthetic-pass",
        "PARKING_REPORTER_ID_NUMBER": "000000000",
    }

    def _inc(self, case_number, status="בטיפול"):
        return {
            "case_number": case_number,
            "createdon": "2026-05-02T08:00:00",
            "streetname": "רחוב הדוגמה",
            "housenumber": "2",
            "subjectname": "חניה אסורה - חניה על מדרכה",
            "statusname": status,
        }

    def test_refresh_unions_archive_and_fetch(self):
        fetched = [
            self._inc("2000-000001", "סיום טיפול"),  # overlaps the archive
            self._inc("2000-999999"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            union_path = Path(tmp) / "union.json"
            with (
                mock.patch.dict("os.environ", self.ENV, clear=False),
                mock.patch(
                    "parking_report.stats_cli._fetch_incidents",
                    return_value=fetched,
                ) as fetch,
            ):
                code = run(
                    [
                        "refresh",
                        "-o",
                        str(out),
                        "--archive",
                        str(FIXTURES / "export_a.json"),
                        "--save-union",
                        str(union_path),
                    ]
                )
            self.assertEqual(code, 0)
            fetch.assert_called_once()
            summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            # 10 archived rows, one overwritten, one appended.
            self.assertEqual(summary["total_incidents"], 11)
            self.assertEqual(summary["inputs"], ["export_a.json", "GetContactCases"])
            union = json.loads(union_path.read_text(encoding="utf-8"))
            self.assertEqual(len(union["incidents"]), 11)
            by_case = {i.get("case_number"): i for i in union["incidents"]}
            self.assertEqual(by_case["2000-000001"]["statusname"], "סיום טיפול")
            self.assertIn("2000-999999", by_case)
            for name in ("monthly.csv", "categories.csv", "streets.csv", "report.html"):
                self.assertTrue((out / name).exists(), name)

    def test_refresh_without_archive_builds_from_fetch_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            with (
                mock.patch.dict("os.environ", self.ENV, clear=False),
                mock.patch(
                    "parking_report.stats_cli._fetch_incidents",
                    return_value=[self._inc("2000-000042")],
                ),
            ):
                code = run(["refresh", "-o", str(out)])
            self.assertEqual(code, 0)
            summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["total_incidents"], 1)

    def test_refresh_missing_env_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {}, clear=True):
                code = run(["refresh", "-o", str(Path(tmp) / "out")])
            self.assertEqual(code, 1)
