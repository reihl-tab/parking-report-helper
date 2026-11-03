"""by_week across a Gregorian year boundary: ISO week-year keys (#242).

`summarize()` keys its trailing `by_week` window by ISO week-year and ISO week
number (`date.isocalendar()`), not by calendar year, so a window that runs
over New Year never emits keys such as "2027-W53". Every test injects
`today` (or stubs the module's own `date.today()` reference); nothing reads
the wall clock, and all incidents are synthetic.

The expected keys are written out literally. They were derived from the ISO
8601 rule (a week belongs to the year of its Thursday) and each list is
cross-checked against `date.isocalendar()` below, independently of the code
under test. Note that 2026 has 53 ISO weeks: 2027-01-01 to 2027-01-03 fall in
2026-W53, and 2027-W01 starts on Monday 2027-01-04.
"""

from __future__ import annotations

import datetime as dt
import types
import unittest
from unittest import mock

from parking_report import stats
from parking_report.stats import summarize

# Window of 12 weeks ending with ISO week 2026-W53 (Mon 2026-12-28 to Sun
# 2027-01-03): today=2027-01-01 is a Friday in calendar year 2027.
WINDOW_ENDING_2026_W53 = [f"2026-W{week:02d}" for week in range(42, 54)]
# today=2027-01-04 (Monday) is 2027-W01: the window spans the ISO year change.
WINDOW_ENDING_2027_W01 = [f"2026-W{week:02d}" for week in range(43, 54)] + ["2027-W01"]
# today=2025-12-29 (Monday) is still calendar 2025 but already ISO 2026-W01;
# 2025 has 52 ISO weeks, so there is no 2025-W53.
WINDOW_ENDING_2026_W01 = [f"2025-W{week:02d}" for week in range(42, 53)] + ["2026-W01"]
# today=2025-12-28 (Sunday) is the last day of 2025-W52.
WINDOW_ENDING_2025_W52 = [f"2025-W{week:02d}" for week in range(41, 53)]

CASES = (
    (dt.date(2027, 1, 1), WINDOW_ENDING_2026_W53),
    (dt.date(2027, 1, 3), WINDOW_ENDING_2026_W53),
    (dt.date(2027, 1, 4), WINDOW_ENDING_2027_W01),
    (dt.date(2025, 12, 29), WINDOW_ENDING_2026_W01),
    (dt.date(2025, 12, 28), WINDOW_ENDING_2025_W52),
)


def incident(createdon: str) -> dict:
    return {"createdon": createdon}


def iso_key(day: dt.date) -> str:
    iso = day.isocalendar()
    return f"{iso.year:04d}-W{iso.week:02d}"


class ExpectedKeysTest(unittest.TestCase):
    """The literal expectations themselves agree with date.isocalendar()."""

    def test_literal_windows_match_isocalendar(self):
        for today, expected in CASES:
            with self.subTest(today=today.isoformat()):
                monday = today - dt.timedelta(days=today.weekday())
                mondays = [
                    monday - dt.timedelta(weeks=back) for back in range(11, -1, -1)
                ]
                self.assertEqual([iso_key(day) for day in mondays], expected)
                self.assertEqual(expected[-1], iso_key(today))


class ByWeekYearBoundaryTest(unittest.TestCase):
    def test_window_keys_use_iso_week_year(self):
        for today, expected in CASES:
            with self.subTest(today=today.isoformat()):
                by_week = summarize([], today=today)["by_week"]
                self.assertEqual(list(by_week), expected)
                self.assertEqual(set(by_week.values()), {0})

    def test_today_2027_01_01_window_ends_in_2026_w53(self):
        by_week = summarize(
            [
                incident("2027-01-01T10:00:00"),  # calendar 2027, ISO 2026-W53
                incident("2026-12-28T08:00:00"),  # Monday of 2026-W53
                incident("2026-10-12T09:00:00"),  # Monday of 2026-W42, first key
                incident("2026-10-11T23:00:00"),  # Sunday of 2026-W41: outside
            ],
            today=dt.date(2027, 1, 1),
        )["by_week"]
        self.assertFalse(any(key.startswith("2027-") for key in by_week))
        self.assertEqual(list(by_week)[0], "2026-W42")
        self.assertEqual(list(by_week)[-1], "2026-W53")
        self.assertEqual(by_week["2026-W53"], 2)
        self.assertEqual(by_week["2026-W42"], 1)
        self.assertNotIn("2026-W41", by_week)
        self.assertEqual(sum(by_week.values()), 3)

    def test_window_spanning_the_iso_year_change(self):
        by_week = summarize(
            [
                incident("2027-01-03T12:00:00"),  # Sunday, last day of 2026-W53
                incident("2027-01-04T00:00:00"),  # Monday, first day of 2027-W01
                incident("2027-01-04T18:30:00"),
            ],
            today=dt.date(2027, 1, 4),
        )["by_week"]
        self.assertEqual(list(by_week)[-2:], ["2026-W53", "2027-W01"])
        self.assertEqual(by_week["2026-W53"], 1)
        self.assertEqual(by_week["2027-W01"], 2)

    def test_late_december_already_in_next_iso_year(self):
        by_week = summarize(
            [
                incident("2025-12-31T12:00:00"),  # calendar 2025, ISO 2026-W01
                incident("2025-12-28T12:00:00"),  # Sunday, ISO 2025-W52
            ],
            today=dt.date(2025, 12, 29),
        )["by_week"]
        self.assertEqual(list(by_week)[0], "2025-W42")
        self.assertEqual(list(by_week)[-1], "2026-W01")
        self.assertNotIn("2025-W53", by_week)
        self.assertNotIn("2025-W01", by_week)
        self.assertEqual(by_week["2026-W01"], 1)
        self.assertEqual(by_week["2025-W52"], 1)

    def test_offset_timestamps_are_bucketed_by_utc_date(self):
        # parse_datetime normalises an explicit offset to UTC before bucketing;
        # a naive timestamp is used as written (the documented behaviour).
        by_week = summarize(
            [
                incident("2027-01-04T00:30:00+02:00"),  # 2027-01-03 22:30 UTC
                incident("2027-01-04T00:30:00"),  # naive: Monday 2027-01-04
            ],
            today=dt.date(2027, 1, 4),
        )["by_week"]
        self.assertEqual(by_week["2026-W53"], 1)
        self.assertEqual(by_week["2027-W01"], 1)


class ByMonthYearBoundaryTest(unittest.TestCase):
    INCIDENTS = [
        incident("2027-01-01T00:30:00"),
        incident("2026-12-31T23:30:00"),
        incident("2027-01-02T09:00:00"),
        incident("2027-01-01T01:00:00+02:00"),  # 2026-12-31 23:00 UTC
    ]

    def test_by_month_uses_calendar_months_in_order(self):
        by_month = summarize(self.INCIDENTS, today=dt.date(2027, 1, 4))["by_month"]
        self.assertEqual(list(by_month.items()), [("2026-12", 2), ("2027-01", 2)])

    def test_by_month_does_not_depend_on_today(self):
        expected = summarize(self.INCIDENTS, today=dt.date(2027, 1, 4))["by_month"]
        for today in (dt.date(2026, 6, 1), dt.date(2027, 1, 1), dt.date(2030, 1, 1)):
            with self.subTest(today=today.isoformat()):
                self.assertEqual(
                    summarize(self.INCIDENTS, today=today)["by_month"], expected
                )


class DefaultTodayTest(unittest.TestCase):
    def test_default_anchor_is_the_modules_date_today(self):
        # Without `today`, summarize() anchors at dt.date.today(), the local
        # date of the machine running the job. Stub only the stats module's own
        # `dt` reference; the real datetime module and wall clock are untouched.
        class FrozenDate(dt.date):
            @classmethod
            def today(cls) -> dt.date:
                return dt.date(2027, 1, 1)

        stub = types.SimpleNamespace(**vars(dt))
        stub.date = FrozenDate
        with mock.patch.object(stats, "dt", stub):
            by_week = summarize([incident("2027-01-01T10:00:00")])["by_week"]
        self.assertIs(stats.dt, dt)
        self.assertEqual(list(by_week), WINDOW_ENDING_2026_W53)
        self.assertEqual(by_week["2026-W53"], 1)


if __name__ == "__main__":
    unittest.main()
