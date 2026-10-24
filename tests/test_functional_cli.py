"""Functional tests: the installed CLI run end to end on the shipped sample input."""

import json
import tempfile
import unittest
from pathlib import Path

from support import REPORTER, SAMPLE, run_cli

FIRST_REPORT = (
    "שלום, אני מדווח על רכב שחונה על המדרכה וחוסם אותה למעבר הולכי רגל. "
    "מיקום: ברחוב הדוגמה 1, ירושלים. תאריך ושעה: 5.10.2026, 06:38. "
    "מספר רכב: 00-000-00 (קופרה). מצורפת תמונה. "
    "אבקש לשלוח פקח אכיפת חניה ולעדכן אותי במספר הפנייה. תודה, דנה"
)


class TestPlainReports(unittest.TestCase):
    def test_sample_produces_one_exact_report_per_car(self):
        result = run_cli(str(SAMPLE), "--city", "ירושלים", "--name", "דנה")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        reports = result.stdout.strip().split("\n\n")
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0], FIRST_REPORT)
        self.assertIn("11-111-11 (הונדה)", reports[1])

    def test_invalid_second_car_fails_cleanly_with_no_partial_output(self):
        cars = json.loads(SAMPLE.read_text(encoding="utf-8"))
        del cars[1]["plate"]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "cars.json")
            path.write_text(json.dumps(cars), encoding="utf-8")
            result = run_cli(str(path))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("car 2", result.stderr)
        self.assertIn("plate", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class TestMunicipalityOutput(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.reporter = Path(self.tmp.name, "reporter.json")
        self.reporter.write_text(
            json.dumps(REPORTER, ensure_ascii=False), encoding="utf-8"
        )

    def test_json_output_has_every_form_field_filled_from_the_right_source(self):
        result = run_cli(
            str(SAMPLE),
            "--municipality",
            "jerusalem",
            "--reporter",
            str(self.reporter),
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        first = json.loads(result.stdout)[0]
        self.assertEqual(first["first_name"], "דנה")
        self.assertEqual(first["id_number"], "000000018")
        self.assertEqual(first["city"], "ירושלים")
        self.assertEqual(first["street"], "הדוגמה")
        self.assertEqual(first["house_number"], "1")
        self.assertIn("00-000-00", first["description"])
        self.assertEqual(first["phone2"], "")  # optional and not supplied

    def test_text_output_lists_fields_in_form_order_with_source_link(self):
        result = run_cli(
            str(SAMPLE), "--municipality", "jerusalem", "--reporter", str(self.reporter)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("https://www.jerusalem.muni.il/he/contactus/106/", result.stdout)
        labels = ["שם פרטי", "שם משפחה", "אמצעי זיהוי", "מספר זיהוי", "תוכן הפנייה"]
        positions = [result.stdout.index(label + ":") for label in labels]
        self.assertEqual(positions, sorted(positions))

    def test_missing_reporter_details_are_marked_not_invented(self):
        result = run_cli(str(SAMPLE), "--municipality", "jerusalem", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        first = json.loads(result.stdout)[0]
        self.assertEqual(first["first_name"], "<חסר>")
        self.assertEqual(first["email"], "<חסר>")

    def test_custom_config_file_replaces_the_bundled_one(self):
        config = Path(self.tmp.name, "city.json")
        config.write_text(
            json.dumps(
                {
                    "city": "עיר לדוגמה",
                    "fields": [
                        {
                            "key": "city",
                            "label": "עיר",
                            "required": True,
                            "source": "config",
                        },
                        {
                            "key": "description",
                            "label": "תיאור",
                            "required": True,
                            "source": "report",
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        result = run_cli(str(SAMPLE), "--municipality", str(config), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(json.loads(result.stdout)[0]), ["city", "description"])

    def test_unknown_municipality_is_a_clean_error(self):
        result = run_cli(str(SAMPLE), "--municipality", "atlantis")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown municipality", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class TestOptionValidation(unittest.TestCase):
    def test_options_that_need_a_municipality_are_rejected_not_ignored(self):
        for flag in (["--reporter", "x.json"], ["--json"]):
            with self.subTest(flag=flag):
                result = run_cli(str(SAMPLE), *flag)
                self.assertEqual(result.returncode, 2)
                self.assertIn("requires --municipality", result.stderr)

    def test_removed_browser_flags_are_rejected(self):
        for flag in (
            ["--fill", "1"],
            ["--photo", "a.jpg"],
            ["--form-url", "https://example.invalid"],
        ):
            result = run_cli(str(SAMPLE), "--municipality", "jerusalem", *flag)
            self.assertEqual(result.returncode, 2)
            self.assertIn("unrecognized arguments", result.stderr)


if __name__ == "__main__":
    unittest.main()
