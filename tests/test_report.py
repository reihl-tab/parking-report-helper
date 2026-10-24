"""Unit tests for report building and validation."""

import unittest

from parking_report import REQUIRED, Car, build_report, build_reports, format_datetime

CAR = {"plate": "00-000-00", "street": "Example 1", "datetime": "2026-01-02 03:04"}


class TestValidation(unittest.TestCase):
    def test_required_fields_are_nonempty_strings(self):
        for field in REQUIRED:
            for value in (None, 123, "", "   "):
                with self.subTest(field=field, value=value):
                    with self.assertRaisesRegex(ValueError, field):
                        build_report({**CAR, field: value})

    def test_invalid_records(self):
        for value in (None, [], "car", 42):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "JSON object"):
                    build_report(value)

    def test_optional_null_fields(self):
        report = build_report({**CAR, "notes": None, "car_type": None})
        self.assertNotIn("None", report)
        self.assertIn("בחניה אסורה", report)

    def test_error_identifies_record(self):
        with self.assertRaisesRegex(ValueError, "car 2:"):
            build_reports([CAR, {}])

    def test_date_edges(self):
        self.assertEqual(format_datetime(" 2024-02-29T23:59 "), "29.2.2024, 23:59")
        for value in ("2025-02-29 10:00", "2026-01-01 24:00", "2026-13-01 00:00"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                format_datetime(value)

    def test_input_is_not_mutated(self):
        record = {**CAR, "plate": " 00-000-00 "}
        original = record.copy()
        self.assertIn("00-000-00", build_report(record))
        self.assertEqual(record, original)

    def test_car_dataclass_accepts_instances(self):
        car = Car.from_mapping(CAR)
        self.assertEqual(build_report(car), build_report(CAR))
        self.assertEqual(car.car_type, "")


if __name__ == "__main__":
    unittest.main()
