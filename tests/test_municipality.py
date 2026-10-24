"""Tests for per-municipality config and form field values."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from parking_report.municipality import (
    MISSING,
    build_submission,
    load_config,
    split_address,
    submission_dict,
)

CAR = {"plate": "00-000-00", "street": "Example 12", "datetime": "2026-01-02 03:04"}
REPORTER = {
    "first_name": "A",
    "last_name": "B",
    "id_type": "תעודת זהות",
    "id_number": "000000000",
    "phone": "0500000000",
    "email": "a@example.com",
}


class TestMunicipality(unittest.TestCase):
    def test_bundled_config_loads(self):
        config = load_config("jerusalem")
        self.assertEqual(config["id"], "jerusalem")
        self.assertEqual(config["channel"]["submission"], "manual")
        self.assertEqual(config["max_attachments"], 3)

    def test_unknown_config_rejected(self):
        for name in ("nowhere", "../x", ""):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    load_config(name)

    def test_config_from_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "c.json"
            path.write_text(
                json.dumps(
                    {
                        "fields": [
                            {
                                "key": "city",
                                "label": "City",
                                "source": "config",
                                "required": True,
                            }
                        ],
                        "city": "X",
                    }
                ),
                encoding="utf-8",
            )
            rows = build_submission(CAR, load_config(str(path)))
        self.assertEqual(submission_dict(rows), {"city": "X"})

    def test_split_address(self):
        self.assertEqual(split_address("הדוגמה 1"), ("הדוגמה", "1"))
        self.assertEqual(split_address("הדוגמה 12א"), ("הדוגמה", "12א"))
        self.assertEqual(split_address("הדוגמה"), ("הדוגמה", ""))

    def test_submission_fields(self):
        rows = build_submission(CAR, load_config("jerusalem"), REPORTER)
        values = submission_dict(rows)
        self.assertEqual(values["city"], "ירושלים")
        self.assertEqual(values["street"], "Example")
        self.assertEqual(values["house_number"], "12")
        self.assertEqual(values["first_name"], "A")
        self.assertIn("00-000-00", values["description"])
        self.assertEqual(values["phone2"], "")
        self.assertNotIn(MISSING, values.values())

    def test_missing_required_reporter_fields_flagged(self):
        values = submission_dict(build_submission(CAR, load_config("jerusalem")))
        self.assertEqual(values["email"], MISSING)
        self.assertEqual(values["phone2"], "")

    def test_invalid_car_rejected(self):
        with self.assertRaises(ValueError):
            build_submission({"plate": "1"}, load_config("jerusalem"))


class TestMunicipalityCLI(unittest.TestCase):
    def run_cli(self, cars, reporter, *options):
        with tempfile.TemporaryDirectory() as directory:
            cars_path = Path(directory) / "cars.json"
            cars_path.write_text(json.dumps(cars), encoding="utf-8")
            args = [sys.executable, "-m", "parking_report", str(cars_path), *options]
            if reporter is not None:
                rep = Path(directory) / "reporter.json"
                rep.write_text(json.dumps(reporter), encoding="utf-8")
                args += ["--reporter", str(rep)]
            return subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )

    def test_text_output(self):
        result = self.run_cli([CAR], REPORTER, "--municipality", "jerusalem")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("יישוב: ירושלים", result.stdout)
        self.assertIn("jerusalem.muni.il/he/contactus/106", result.stdout)
        self.assertIn("שם פרטי: A", result.stdout)

    def test_json_output(self):
        result = self.run_cli(
            [CAR, CAR], REPORTER, "--municipality", "jerusalem", "--json"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["house_number"], "12")

    def test_without_reporter_flags_missing(self):
        result = self.run_cli([CAR], None, "--municipality", "jerusalem")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(MISSING, result.stdout)

    def test_errors_fail_cleanly(self):
        cases = [
            ([CAR], None, ("--municipality", "nowhere")),
            ([{"plate": "1"}], None, ("--municipality", "jerusalem")),
            ({"a": 1}, None, ("--municipality", "jerusalem")),
            ([CAR], ["not", "object"], ("--municipality", "jerusalem")),
        ]
        for cars, reporter, options in cases:
            with self.subTest(options=options, cars=cars):
                result = self.run_cli(cars, reporter, *options)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertIn("error:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
