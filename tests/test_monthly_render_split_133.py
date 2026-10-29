"""Unit tests for the #133 PR B rendering helpers of monthly_draft."""

import unittest
from collections import Counter

from parking_report import monthly as m


def row(**changes):
    base = {
        "folder_id": "ZZ-1",
        "filed_date": "2026-05-03",
        "street": "ZZ street",
        "event_type": "sidewalk",
        "municipal_ref": "",
        "ref_mapping": "",
    }
    base.update(changes)
    return base


class TestSummaryLines(unittest.TestCase):
    def test_counts_and_shape(self):
        rows = [
            row(ref_mapping="verified", municipal_ref="1"),
            row(),
            row(ref_mapping="x"),
        ]
        lines = m._summary_lines("2026-05", rows)
        self.assertEqual(lines[0], "# טיוטה: סיכום דיווחים לחודש 2026-05")
        self.assertIn("רשומות דיווח ביומן: 3", lines)
        self.assertIn("שיוך מספר פנייה מאומת: 1", lines)
        self.assertIn("שיוך חסר או לא מאומת: 2", lines)
        self.assertEqual(len(lines), 11)
        self.assertEqual(lines[-1], "")

    def test_only_exact_verified_counts(self):
        rows = [row(ref_mapping=v) for v in ("Verified", " verified", "verified ")]
        lines = m._summary_lines("2026-05", rows)
        self.assertIn("שיוך מספר פנייה מאומת: 0", lines)

    def test_empty(self):
        lines = m._summary_lines("2026-05", [])
        self.assertIn("רשומות דיווח ביומן: 0", lines)
        self.assertIn("שיוך חסר או לא מאומת: 0", lines)


class TestCountTable(unittest.TestCase):
    def test_order_most_first_then_key(self):
        lines = m._count_table("T", "C", Counter({"b": 2, "a": 2, "c": 5, "d": 1}))
        self.assertEqual(lines[:4], ["## T", "", "| C | דיווחים |", "| --- | ---: |"])
        self.assertEqual(
            lines[4:], ["| c | 5 |", "| a | 2 |", "| b | 2 |", "| d | 1 |"]
        )

    def test_cells_are_escaped(self):
        lines = m._count_table("T", "C", Counter({"x|y": 1}))
        self.assertEqual(lines[-1], f"| {m._cell('x|y')} | 1 |")
        self.assertNotIn("| x|y |", lines)

    def test_empty(self):
        self.assertEqual(len(m._count_table("T", "C", Counter())), 4)


class TestReferenceTable(unittest.TestCase):
    def test_rows_raw_values_and_status(self):
        rows = [
            row(street=" ZZ street ", municipal_ref="7", ref_mapping="verified"),
            row(folder_id="ZZ-2", municipal_ref="", ref_mapping="guess"),
        ]
        lines = m._reference_table(rows)
        self.assertEqual(lines[2], "| תאריך | רחוב | סוג אירוע | מספר פנייה | שיוך |")
        expected = (
            "| "
            + " | ".join(
                m._cell(v)
                for v in ("2026-05-03", " ZZ street ", "sidewalk", "7", "מאומת")
            )
            + " |"
        )
        self.assertEqual(lines[4], expected)
        self.assertTrue(
            lines[5].endswith(f"| {m._cell('חסר')} | {m._cell('לא מאומת')} |")
        )
        self.assertEqual(len(lines), 6)


class TestStripVsRawQuirk(unittest.TestCase):
    """Pinned on purpose (#133): counts strip, the reference table does not."""

    def test_through_monthly_draft(self):
        out = m.monthly_draft(
            [row(street=" ZZ street"), row(folder_id="ZZ-2", street="ZZ street ")],
            "2026-05",
        )
        self.assertIn("| ZZ street | 2 |", out)
        self.assertIn(m._cell(" ZZ street"), out)

    def test_event_type_counts_strip_too(self):
        out = m.monthly_draft(
            [row(event_type=" ramp"), row(folder_id="ZZ-2", event_type="ramp")],
            "2026-05",
        )
        self.assertIn("| ramp | 2 |", out)
        self.assertIn(m._cell(" ramp"), out)


if __name__ == "__main__":
    unittest.main()
