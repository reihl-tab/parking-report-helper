"""Synthetic-fixture tests for the local 106 statistics module (issue #210)."""

from __future__ import annotations

import unittest
from pathlib import Path

from parking_report.stats import (
    NO_STREET,
    StatsError,
    _quantile,
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

    def test_by_week_trailing_and_hotspots(self):
        by_week = self.summary["by_week"]
        self.assertTrue(all(key.startswith("2026-W") for key in by_week))
        self.assertEqual(sum(by_week.values()), 12)
        self.assertEqual(next(iter(self.summary["hotspots"])), "רחוב הדוגמה 1")

    def test_streetless_incidents_are_not_hotspots(self):
        # Fixture: one incident has streetname null; it must not appear as a spot.
        self.assertNotIn("(ללא רחוב)", self.summary["hotspots"])
        self.assertFalse(
            any(spot.startswith(NO_STREET) for spot in self.summary["hotspots"])
        )

    def test_hotspots_below_the_minimum_count_floor_are_dropped(self):
        # Fixture: "רחוב הסינתטי 1" appears twice - under the default floor of 3.
        self.assertNotIn("רחוב הסינתטי 1", self.summary["hotspots"])
        self.assertEqual(
            list(self.summary["hotspots"]), ["רחוב הדוגמה 1", "שדרות הבדיקה 1"]
        )
        lowered = summarize(self.incidents, min_hotspot_count=2)
        self.assertIn("רחוב הסינתטי 1", lowered["hotspots"])
        self.assertIsNone(self.summary["by_neighborhood"])

    def test_neighborhood_mapping(self):
        mapping = {"רחוב הדוגמה": "שכונת הדוגמה", "שדרות הבדיקה": "שכונת הבדיקה"}
        summary = summarize(self.incidents, neighborhoods=mapping)
        areas = summary["by_neighborhood"]
        self.assertEqual(areas["שכונת הדוגמה"], 6)
        self.assertEqual(areas["(אחר)"], 4)

    def test_top_streets_limit(self):
        summary = summarize(self.incidents, top_streets=2)
        self.assertEqual(len(summary["by_street"]), 2)


class QuantileTest(unittest.TestCase):
    def test_nearest_rank(self):
        values = [1.0, 2.0, 3.0, 4.0]
        self.assertEqual(_quantile(values, 0.5), 2.0)
        self.assertEqual(_quantile(values, 0.25), 1.0)
        self.assertEqual(_quantile(values, 0.75), 3.0)
        self.assertEqual(_quantile([1.0, 2.0, 3.0], 0.5), 2.0)
        self.assertEqual(_quantile([], 0.5), 0.0)
        self.assertEqual(_quantile([5.0], 0.75), 5.0)

    def test_summary_median_p25_p75(self):
        incidents = [
            {
                "createdon": f"2026-06-0{day}T08:00:00",
                "ClosedDateIncident": f"2026-06-0{day + days}T08:00:00",
                "subjectname": "קטגוריה",
                "statusname": "סיום טיפול",
            }
            for day, days in enumerate((1, 2, 3, 4), start=1)
        ]
        closure = summarize(incidents)["closure"]
        self.assertEqual(closure["median_days"], 2.0)
        self.assertEqual(closure["p25_days"], 1.0)
        self.assertEqual(closure["p75_days"], 3.0)

    def test_median_days_by_category_ties_sort_by_name(self):
        incidents = [
            {
                "createdon": "2026-06-01T08:00:00",
                "ClosedDateIncident": "2026-06-02T08:00:00",
                "subjectname": category,
                "statusname": "סיום טיפול",
            }
            for category in ("ב קטגוריה", "א קטגוריה")
        ]
        medians = summarize(incidents)["closure"]["median_days_by_category"]
        self.assertEqual(list(medians), ["א קטגוריה", "ב קטגוריה"])


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
