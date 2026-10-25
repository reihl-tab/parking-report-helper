"""Synthetic monthly-draft unit and real-CLI tests; no network or private fixtures."""

import csv
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from parking_report.monthly import REQUIRED, monthly_draft


def row(**changes):
    return (
        {
            "folder_id": "example-a",
            "filed_date": "2026-10-02",
            "street": "רחוב לדוגמה",
            "event_type": "sidewalk",
            "municipal_ref": "2610-000001",
            "ref_mapping": "unverified_fifo",
            "plate": "SYNTHETIC-PRIVATE-PLATE",
            "notes": "PRIVATE-NOTES",
        }
        | changes
    )


class MonthlyTests(unittest.TestCase):
    def test_count_filter_sort_and_no_plate(self):
        rows = [
            row(
                folder_id="b",
                street="רחוב ב",
                municipal_ref="2610-000002",
                ref_mapping="verified",
            ),
            row(),
            row(folder_id="c", street="רחוב ב", municipal_ref=""),
            row(folder_id="other", filed_date="2026-11-01"),
        ]
        output = monthly_draft(rows, "2026-10")
        self.assertIn("רשומות דיווח ביומן: 3", output)
        self.assertIn("שיוך מספר פנייה מאומת: 1", output)
        self.assertIn("שיוך חסר או לא מאומת: 2", output)
        self.assertLess(
            output.index("| רחוב ב | 2 |"), output.index("| רחוב לדוגמה | 1 |")
        )
        self.assertIn("2610-000001 | לא מאומת", output)
        self.assertIn("2610-000002 | מאומת", output)
        self.assertIn("חסר | לא מאומת", output)
        self.assertNotIn("SYNTHETIC-PRIVATE-PLATE", output)
        self.assertNotIn("PRIVATE-NOTES", output)
        self.assertNotIn("example-a", output)
        self.assertEqual(output, monthly_draft(reversed(rows), "2026-10"))

    def test_empty(self):
        self.assertIn("אין רשומות", monthly_draft([], "2026-10"))

    def test_unknown_mapping_is_unverified(self):
        for mapping in ("", "pending", "VERIFIED", "unverified_fifo"):
            self.assertIn(
                "2610-000001 | לא מאומת",
                monthly_draft([row(ref_mapping=mapping)], "2026-10"),
            )

    def test_reject_months_dates_and_bad_records(self):
        for month in ("2026-13", "2026-00", "26-10", "2026-1", "0000-01"):
            with self.subTest(month=month), self.assertRaises(ValueError):
                monthly_draft([], month)
        for changes in (
            {"filed_date": "2026-02-30"},
            {"filed_date": "2026-1-01"},
            {"folder_id": ""},
            {"street": " "},
            {"event_type": ""},
            {"street": "x\ny"},
            {"street": "x\u202ey"},
            {"municipal_ref": "broken"},
            {"municipal_ref": "", "ref_mapping": "verified"},
            {"street": None},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                monthly_draft([row(**changes)], "2026-10")
        with self.assertRaises(ValueError):
            monthly_draft([{}], "2026-10")

    def test_duplicate_identity_or_reference_blocks(self):
        for second in (row(municipal_ref="2610-000002"), row(folder_id="b")):
            with self.assertRaises(ValueError):
                monthly_draft([row(), second], "2026-10")

    def test_table_escaping(self):
        output = monthly_draft([row(street="רחוב | <script>")], "2026-10")
        self.assertIn("רחוב \\| &lt;script&gt;", output)
        self.assertNotIn("<script>", output)

    def test_no_network(self):
        with patch("socket.socket", side_effect=AssertionError("Network forbidden")):
            self.assertIn("טיוטה", monthly_draft([row()], "2026-10"))

    def run_cli(self, text, month="2026-10"):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.csv"
            path.write_text(text, encoding="utf-8")
            return subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "parking_report.monthly",
                    str(path),
                    "--month",
                    month,
                ],
                capture_output=True,
                text=True,
                check=False,
            )

    def csv_text(self, rows):
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=sorted(REQUIRED))
        writer.writeheader()
        writer.writerows({key: r[key] for key in REQUIRED} for r in rows)
        return stream.getvalue()

    def test_real_cli_and_bom(self):
        result = self.run_cli("\ufeff" + self.csv_text([row()]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, monthly_draft([row()], "2026-10"))
        self.assertEqual(result.stderr, "")

    def test_real_cli_no_partial_output_or_private_error(self):
        for text in (
            "",
            "wrong,columns\na,b\n",
            self.csv_text([row(), row(folder_id="PRIVATE-BAD-ROW")]),
        ):
            result = self.run_cli(text)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertNotIn("PRIVATE-BAD-ROW", result.stderr)
            self.assertNotIn("2610-000001", result.stderr)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
