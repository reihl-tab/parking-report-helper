"""Synthetic-fixture tests for the local 106 statistics module (issue #210)."""

from __future__ import annotations

import unittest
from pathlib import Path

from parking_report.stats import (
    NO_STREET,
    StatsError,
    _quantile,
    closure_days,
    display_plate,
    load_incidents,
    parse_datetime,
    plate_digits,
    plates_summary,
    summarize,
    union_with_fetched,
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


class DisplayPlateTest(unittest.TestCase):
    def test_compact_seven_digit_gets_dashes(self):
        self.assertEqual(display_plate("1234567"), "12-345-67")

    def test_compact_eight_digit_gets_dashes(self):
        self.assertEqual(display_plate("44556677"), "445-56-677")


class PlateDigitsTest(unittest.TestCase):
    def test_standard_forms_accepted(self):
        self.assertEqual(plate_digits("1234567"), "1234567")
        self.assertEqual(plate_digits("44556677"), "44556677")
        self.assertEqual(plate_digits("12-345-67"), "1234567")
        self.assertEqual(plate_digits("445-56-677"), "44556677")
        self.assertEqual(plate_digits("  1234567 "), "1234567")

    def test_nonstandard_plates_rejected(self):
        for bad in (
            "99-999-ז",  # Hebrew-letter special format: not published
            "=1234567",  # spreadsheet formula prefix
            "+1234567",
            "@1234567",
            "123",  # too short
            "123456789",  # too long
            "12-34-567",  # dashes in non-standard positions
            "abc-defg-h",
            "1234567\u202e",  # bidi control
            "",
        ):
            self.assertIsNone(plate_digits(bad), bad)


class PlatesSummaryTest(unittest.TestCase):
    """Repeat-plate aggregate from a synthetic ledger (issue #220).

    Every plate below is fake; real numbers must never enter fixtures.
    """

    def _write_ledger(
        self, tmp: Path, rows: list[tuple[str, str, str]], *, header: str | None = None
    ) -> Path:
        import csv as _csv

        path = tmp / "ledger.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = _csv.writer(handle)
            writer.writerow(
                header.split(",")
                if header
                else ["filed_date", "plate", "municipal_ref", "event_type"]
            )
            writer.writerows(rows)
        return path

    def test_threshold_grouping_and_ordering(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            ledger = self._write_ledger(
                Path(td),
                [
                    ("2026-10-01", "1122233", "2601-000001"),
                    ("2026-10-02", "11-222-33", "2601-000002"),  # same car, dashed
                    ("2026-10-05", "1122233", "2601-000003"),
                    ("2026-09-20", "44556677", "2601-000004"),
                    ("2026-10-03", "44556677", "2601-000005"),
                    ("2026-10-04", "44556677", "2601-000006"),
                    ("2026-10-06", "88-977-66", "2601-000007"),
                    ("2026-10-07", "88-977-66", "2601-000008"),
                    ("2026-10-08", "88-977-66", "2601-000009"),
                    ("2026-10-09", "5566677", "2601-000010"),  # below threshold
                    ("2026-10-09", "", "2601-000011"),  # no plate: skipped
                ],
            )
            rows, _ = plates_summary(ledger)
        self.assertEqual(
            rows,
            [
                {"plate": "11-222-33", "reports": 3},
                {"plate": "445-56-677", "reports": 3},
                {"plate": "88-977-66", "reports": 3},
            ],
        )

    def test_same_case_recorded_twice_counts_once(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            ledger = self._write_ledger(
                Path(td),
                [
                    ("2026-10-01", "1122233", "2601-000001"),
                    ("2026-10-01", "1122233", "2601-000001"),  # duplicate recording
                    ("2026-10-02", "1122233", "2601-000002"),
                    ("2026-10-03", "1122233", "2601-000003"),
                ],
            )
            rows, _ = plates_summary(ledger)
            self.assertEqual(rows, [{"plate": "11-222-33", "reports": 3}])

    def test_missing_plate_column_is_a_loud_error(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            ledger = self._write_ledger(
                Path(td), [("2026-10-01", "חניה")], header="filed_date,event_type"
            )
            with self.assertRaises(StatsError):
                plates_summary(ledger)

    def test_missing_filed_date_column_is_a_loud_error(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            ledger = self._write_ledger(
                Path(td), [("1122233", "חניה")], header="plate,event_type"
            )
            with self.assertRaises(StatsError):
                plates_summary(ledger)

    def test_partial_id_overlap_is_one_report(self):
        # Reviewer edge case: a report first recorded with only folder_id,
        # later with municipal_ref + the same folder_id, plus one other
        # report -> 2 reports, not 3.
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ledger.csv"
            path.write_text(
                "filed_date,plate,folder_id,municipal_ref\n"
                "2026-10-01,1122233,folder-a,\n"
                "2026-10-01,1122233,folder-a,2601-000001\n"
                "2026-10-02,1122233,folder-b,2601-000002\n",
                encoding="utf-8",
            )
            rows, _ = plates_summary(path, min_count=2)
            self.assertEqual(rows, [{"plate": "11-222-33", "reports": 2}])
            rows, _ = plates_summary(path)
            self.assertEqual(rows, [])

    def test_id_chain_across_rows_is_one_report(self):
        # folder-only row + bridging row + ref-only row: one report.
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ledger.csv"
            path.write_text(
                "filed_date,plate,folder_id,municipal_ref\n"
                "2026-10-01,1122233,folder-a,\n"
                "2026-10-01,1122233,folder-a,2601-000001\n"
                "2026-10-01,1122233,,2601-000001\n",
                encoding="utf-8",
            )
            rows, _ = plates_summary(path, min_count=1)
            self.assertEqual(rows, [{"plate": "11-222-33", "reports": 1}])

    def test_rows_without_any_id_are_excluded(self):
        # Reviewer blocker: an id-less copy of an identified report must
        # not inflate the count - exclude id-less rows entirely and
        # report how many were skipped.
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ledger.csv"
            path.write_text(
                "filed_date,plate,folder_id,municipal_ref,street\n"
                "2026-10-01,1122233,folder-a,2601-000001,רחוב בדיקה\n"
                "2026-10-02,1122233,folder-b,2601-000002,רחוב בדיקה\n"
                "2026-10-01,1122233,,,רחוב בדיקה\n"  # id-less copy: excluded
                "2026-10-01,1122233,,,רחוב בדיקה\n",
                encoding="utf-8",
            )
            rows, skipped = plates_summary(path, min_count=2)
            self.assertEqual(rows, [{"plate": "11-222-33", "reports": 2}])
            self.assertEqual(skipped, 2)
            # id-less rows alone can never qualify a plate
            rows, skipped = plates_summary(path, min_count=3)
            self.assertEqual(rows, [])

    def test_invalid_filed_dates_are_excluded(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ledger.csv"
            path.write_text(
                "filed_date,plate,municipal_ref\n"
                "2026-10-01,1122233,2601-000001\n"
                "2026-02-30,1122233,2601-000002\n"  # impossible date
                ",1122233,2601-000003\n"  # blank date
                "next Tuesday,1122233,2601-000004\n"
                "2026-10-02,1122233,2601-000005\n",
                encoding="utf-8",
            )
            rows, skipped = plates_summary(path, min_count=1)
            self.assertEqual(rows, [{"plate": "11-222-33", "reports": 2}])
            self.assertEqual(skipped, 3)

    def test_malformed_csv_is_a_loud_error(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ledger.csv"
            path.write_text(
                "filed_date,plate,municipal_ref\n"
                '2026-10-01,"1122233,2601-000001\n',  # unterminated quote
                encoding="utf-8",
            )
            with self.assertRaises(StatsError):
                plates_summary(path)

    def test_invalid_utf8_is_a_loud_error(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ledger.csv"
            path.write_bytes(b"filed_date,plate\n2026-10-01,\xff\xfe\n")
            with self.assertRaises(StatsError):
                plates_summary(path)

    def test_min_count_is_configurable(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            ledger = self._write_ledger(
                Path(td),
                [
                    ("2026-10-01", "1122233", "2601-000001"),
                    ("2026-10-02", "1122233", "2601-000002"),
                ],
            )
            rows, _ = plates_summary(ledger)
            self.assertEqual(rows, [])
            rows, _ = plates_summary(ledger, min_count=2)
            self.assertEqual(len(rows), 1)


class UnionWithFetchedTest(unittest.TestCase):
    """union_with_fetched: fetched rows win on overlap (#232)."""

    def _inc(self, case_number, status="בטיפול"):
        return {
            "case_number": case_number,
            "createdon": "2026-05-01T08:00:00",
            "streetname": "רחוב הדוגמה",
            "housenumber": "1",
            "subjectname": "חניה אסורה - חניה על מדרכה",
            "statusname": status,
        }

    def test_fetched_wins_on_overlap(self):
        archived = [self._inc("1000-1", "סיום טיפול"), self._inc("1000-2")]
        fetched = [self._inc("1000-2", "סיום טיפול"), self._inc("1000-3")]
        merged, overlaps = union_with_fetched(archived, fetched)
        self.assertEqual(overlaps, 1)
        self.assertEqual(
            [i["case_number"] for i in merged], ["1000-1", "1000-2", "1000-3"]
        )
        self.assertEqual(merged[1]["statusname"], "סיום טיפול")

    def test_archived_row_without_case_number_is_kept(self):
        archived = [{"streetname": "רחוב הדוגמה", "createdon": "2026-05-01T08:00:00"}]
        merged, overlaps = union_with_fetched(archived, [self._inc("1000-9")])
        self.assertEqual((len(merged), overlaps), (2, 0))
        self.assertIsNone(merged[0].get("case_number"))

    def test_fetched_row_without_case_number_raises(self):
        with self.assertRaises(StatsError):
            union_with_fetched([], [{"streetname": "רחוב הדוגמה"}])

    def test_fetched_internal_duplicates_are_not_archived_refreshes(self):
        # A case_number repeated WITHIN the fetched rows overwrites its
        # earlier fetched copy but is not an archived-row refresh (#237).
        fetched = [self._inc("1000-5"), self._inc("1000-5", "סיום טיפול")]
        merged, overlaps = union_with_fetched([], fetched)
        self.assertEqual((len(merged), overlaps), (1, 0))
        self.assertEqual(merged[0]["statusname"], "סיום טיפול")

    def test_fetched_duplicate_of_archive_counts_once(self):
        fetched = [self._inc("1000-6", "סיום טיפול"), self._inc("1000-6")]
        merged, overlaps = union_with_fetched([self._inc("1000-6")], fetched)
        self.assertEqual((len(merged), overlaps), (1, 1))

    def test_whitespace_case_numbers_match(self):
        merged, overlaps = union_with_fetched(
            [self._inc(" 1000-4 ")], [self._inc("1000-4", "סיום טיפול")]
        )
        self.assertEqual((len(merged), overlaps), (1, 1))
        self.assertEqual(merged[0]["statusname"], "סיום טיפול")
