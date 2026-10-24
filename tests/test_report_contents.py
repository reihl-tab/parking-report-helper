"""Report content tests."""

import json
import os
import unittest

import parking_report as pr

CAR = {
    "plate": "00-000-00",
    "car_type": "קופרה",
    "street": "הדוגמה 1",
    "datetime": "2026-10-05 06:38",
    "notes": "על המדרכה",
}


class TestReport(unittest.TestCase):
    def test_format_datetime(self):
        self.assertEqual(pr.format_datetime("2026-10-05 06:38"), "5.10.2026, 06:38")
        self.assertEqual(pr.format_datetime("2026-10-05T05:30"), "5.10.2026, 05:30")

    def test_bad_datetime(self):
        with self.assertRaises(ValueError):
            pr.format_datetime("yesterday")

    def test_report_contents(self):
        t = pr.build_report(CAR, city="ירושלים", name="דנה")
        for s in (
            "00-000-00 (קופרה)",
            "ברחוב הדוגמה 1, ירושלים",
            "5.10.2026, 06:38",
            "על המדרכה",
            "מצורפת תמונה",
            "תודה, דנה",
        ):
            self.assertIn(s, t)

    def test_optional_fields(self):
        car = {"plate": "11-111-11", "street": "א", "datetime": "2026-10-05 05:30"}
        t = pr.build_report(car, with_photo=False)
        self.assertIn("בחניה אסורה", t)
        self.assertNotIn("מצורפת תמונה", t)
        self.assertTrue(t.endswith("תודה"))

    def test_missing_field(self):
        with self.assertRaises(ValueError):
            pr.build_report({"plate": "00-000-00"})

    def test_sample_file(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample.json"
        )
        with open(path, encoding="utf-8") as f:
            reports = pr.build_reports(json.load(f))
        self.assertEqual(len(reports), 2)
        self.assertIn("11-111-11", reports[1])

    def test_not_a_list(self):
        with self.assertRaises(ValueError):
            pr.build_reports({"plate": "x"})


if __name__ == "__main__":
    unittest.main()
