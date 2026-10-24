"""Synthetic tests only; no municipal requests, private fixtures or sends."""

import csv
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from parking_report.status import REQUIRED, status_draft


def row(**changes):
    return (
        dict(
            folder_id="synthetic-a",
            filed_date="2026-10-01",
            street="רחוב לדוגמה",
            event_type="sidewalk",
            plate="00-000-00",
            municipal_ref="2610-000001",
            ref_mapping="verified_receipt",
            receipt_evidence="synthetic-receipt.json",
            municipal_response="no_reply",
            response_checked_at="2026-10-08",
            response_evidence="synthetic-mail-check",
        )
        | changes
    )


class StatusTests(unittest.TestCase):
    def test_week_boundary(self):
        self.assertIn("טיוטות תזכורת: 1", status_draft([row()], "2026-10-08"))
        self.assertIn(
            "טרם חלף שבוע",
            status_draft([row(response_checked_at="2026-10-07")], "2026-10-07"),
        )
        self.assertIn(
            "טיוטות תזכורת: 1",
            status_draft([row(response_checked_at="2026-10-09")], "2026-10-09"),
        )

    def test_missing_response_is_unknown_not_overdue(self):
        output = status_draft(
            [row(municipal_response="", response_checked_at="", response_evidence="")],
            "2026-10-09",
        )
        self.assertIn("מענה לא נבדק", output)
        self.assertIn("טיוטות תזכורת: 0", output)

    def test_received_suppresses_reminder(self):
        output = status_draft([row(municipal_response="received")], "2026-10-09")
        self.assertIn("מענה מתועד", output)
        self.assertIn("טיוטות תזכורת: 0", output)

    def test_stale_check_and_same_day_reminder_suppress(self):
        self.assertIn("נדרשת בדיקת מענה עדכנית", status_draft([row()], "2026-10-09"))
        self.assertIn(
            "טיוטות תזכורת: 0",
            status_draft([row(last_reminder_at="2026-10-08")], "2026-10-08"),
        )

    def test_unverified_or_missing_evidence_blocks_case_draft(self):
        for changes in (
            {"ref_mapping": "unverified_fifo"},
            {"ref_mapping": "owner_stated_order"},
            {"ref_mapping": "verified"},
            {"receipt_evidence": ""},
            {"municipal_ref": ""},
        ):
            output = status_draft([row(**changes)], "2026-10-08")
            self.assertIn("שיוך הפנייה דורש אימות", output)
            self.assertIn("טיוטות תזכורת: 0", output)

    def test_private_table_columns_and_determinism(self):
        a = row(
            x_post_url="https://example.invalid/x",
            facebook_post_url="https://example.invalid/fb",
        )
        b = row(folder_id="synthetic-b", municipal_ref="2610-000002")
        output = status_draft([b, a], "2026-10-08")
        self.assertEqual(output, status_draft([a, b], "2026-10-08"))
        for text in (
            "00-000-00",
            "https://example.invalid/x",
            "https://example.invalid/fb",
            "synthetic-a",
        ):
            self.assertIn(text, output)
        self.assertNotIn("synthetic-mail-check", output)
        self.assertNotIn("synthetic-receipt.json", output)

    def test_bad_input_has_no_partial_result(self):
        bad = [
            {"filed_date": "2026-02-30"},
            {"filed_date": "2026-10-09"},
            {"street": "x\ny"},
            {"street": "x\u202ey"},
            {"plate": None},
            {"municipal_response": "maybe"},
            {"response_checked_at": "2026-10-10"},
            {"response_checked_at": "2026-09-30"},
            {"response_evidence": ""},
            {"response_checked_at": ""},
            {"last_reminder_at": "2026-10-10"},
        ]
        for changes in bad:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                status_draft(
                    [row(), row(folder_id="b", municipal_ref="2610-000002", **changes)],
                    "2026-10-08",
                )
        for rows in ([row(), row()], [row(), row(folder_id="b")], [{}]):
            with self.assertRaises(ValueError):
                status_draft(rows, "2026-10-08")
        with self.assertRaises(ValueError):
            status_draft([], "2026-13-01")

    def test_escape_markup_and_no_network(self):
        with patch("socket.socket", side_effect=AssertionError("No network")):
            output = status_draft(
                [row(street="[x](https://example.invalid) | <img>")], "2026-10-08"
            )
        self.assertIn("\\[x\\]\\(https://example.invalid\\)", output)
        self.assertIn("\\| &lt;img&gt;", output)

    def test_empty_table(self):
        self.assertIn("טיוטות תזכורת: 0", status_draft([], "2026-10-08"))

    def run_cli(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.csv"
            path.write_text(text, encoding="utf-8")
            return subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "parking_report.status",
                    str(path),
                    "--as-of",
                    "2026-10-08",
                ],
                capture_output=True,
                text=True,
            )

    def test_real_cli_legacy_csv_and_bom(self):
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=sorted(REQUIRED))
        writer.writeheader()
        writer.writerow({k: row()[k] for k in REQUIRED})
        result = self.run_cli("\ufeff" + stream.getvalue())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("מענה לא נבדק", result.stdout)
        self.assertIn("טיוטות תזכורת: 0", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_real_cli_failure_has_no_private_values(self):
        result = self.run_cli("wrong\nPRIVATE-VALUE\n")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("PRIVATE-VALUE", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
