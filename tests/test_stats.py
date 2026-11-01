"""Synthetic-fixture tests for the local 106 statistics module (issue #210)."""

from __future__ import annotations

import unittest
from pathlib import Path

from parking_report.stats import (
    NO_STREET,
    StatsError,
    closure_days,
    load_incidents,
    parse_datetime,
    summarize,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "stats"


class ParseDatetimeTest(unittest.TestCase):
    def test_plain_iso(self):
        self.assertEqual(
            parse_datetime("2026-05-01T08:00:00").isoformat(), "2026-05-01T08:00:00"
        )

    def test_zulu_and_offset_are_normalised(self):
        self.assertEqual(
            parse_datetime("2026-05-01T08:00:00Z"),
            parse_datetime("2026-05-01T10:00:00+02:00"),
        )

    def test_junk_and_none(self):
        self.assertIsNone(parse_datetime(None))
        self.assertIsNone(parse_datetime(""))
        self.assertIsNone(parse_datetime("not-a-date"))
        self.assertIsNone(parse_datetime(42))


class LoadIncidentsTest(unittest.TestCase):
    def test_single_file(self):
        incidents, duplicates = load_incidents([FIXTURES / "export_a.json"])
        self.assertEqual(len(incidents), 10)
        self.assertEqual(duplicates, 0)

    def test_merge_dedupes_by_case_number_first_wins(self):
        incidents, duplicates = load_incidents(
            [FIXTURES / "export_a.json", FIXTURES / "export_b.json"]
        )
        self.assertEqual(duplicates, 1)
        self.assertEqual(len(incidents), 13)
        by_case = [i for i in incidents if i.get("case_number") == "2000-000010"]
        self.assertEqual(len(by_case), 1)

    def test_incident_without_case_number_is_kept(self):
        incidents, _ = load_incidents([FIXTURES / "export_b.json"])
        self.assertTrue(
            any(i.get("caseId") == "synthetic-no-number" for i in incidents)
        )

    def test_broken_json_rejected(self):
        with self.assertRaises(StatsError):
            load_incidents([FIXTURES / "broken.json"])

    def test_wrong_shape_rejected(self):
        with self.assertRaises(StatsError):
            load_incidents([FIXTURES / "wrong_shape.json"])

    def test_missing_file_rejected(self):
        with self.assertRaises(StatsError):
            load_incidents([FIXTURES / "does-not-exist.json"])


class SummarizeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.incidents, cls.duplicates = load_incidents(
            [FIXTURES / "export_a.json", FIXTURES / "export_b.json"]
        )
        cls.summary = summarize(cls.incidents)

    def test_totals_and_range(self):
        self.assertEqual(self.summary["total_incidents"], 13)
        self.assertEqual(self.summary["date_range"]["first"], "2026-05-01")
        self.assertEqual(self.summary["date_range"]["last"], "2026-07-04")

    def test_by_month(self):
        self.assertEqual(
            self.summary["by_month"],
            {"2026-05": 4, "2026-06": 4, "2026-07": 4},
        )

    def test_by_category_sorted_desc(self):
        categories = self.summary["by_category"]
        self.assertEqual(next(iter(categories)), "חניה אסורה - חניה על מדרכה")
        self.assertEqual(categories["חניה אסורה - חניה על מדרכה"], 9)
        self.assertEqual(categories["בחינת בקשה להתקנת עמוד מחסום לחניה"], 2)

    def test_null_street_bucketed(self):
        self.assertEqual(self.summary["by_street"].get("(ללא רחוב)"), 1)
        self.assertIn(NO_STREET, self.summary["by_street"])

    def test_by_status(self):
        self.assertEqual(self.summary["by_status"]["סיום טיפול"], 9)
        self.assertEqual(self.summary["by_status"]["בטיפול"], 4)

    def test_closure_math(self):
        closure = self.summary["closure"]
        self.assertEqual(closure["closed_count"], 9)
        self.assertEqual(closure["max_days"], 19.0)
        self.assertEqual(closure["buckets"]["עד שעה"], 3)
        self.assertEqual(closure["buckets"]["מעל שבוע"], 2)
        medians = closure["median_days_by_category"]
        self.assertIn("חניה אסורה - חניה על מדרכה", medians)

    def test_empty_input(self):
        summary = summarize([])
        self.assertEqual(summary["total_incidents"], 0)
        self.assertIsNone(summary["date_range"]["first"])
        self.assertEqual(summary["closure"]["closed_count"], 0)

    def test_top_streets_limit(self):
        summary = summarize(self.incidents, top_streets=2)
        self.assertEqual(len(summary["by_street"]), 2)


class ClosureDaysTest(unittest.TestCase):
    def test_never_negative(self):
        self.assertEqual(
            closure_days(
                {
                    "createdon": "2026-05-02T10:00:00",
                    "ClosedDateIncident": "2026-05-01T10:00:00",
                }
            ),
            0.0,
        )

    def test_unclosed_is_none(self):
        self.assertIsNone(closure_days({"createdon": "2026-05-02T10:00:00"}))


if __name__ == "__main__":
    unittest.main()
