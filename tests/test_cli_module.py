"""In-process coverage for cli.py and __main__.py (the functional tests run a subprocess, invisible to coverage)."""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from parking_report import cli

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "sample.json"
INTAKE_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "intake"
REPORTER = {
    "first_name": "דנה",
    "last_name": "לוי",
    "id_type": "תעודת זהות",
    "id_number": "000000018",
    "phone": "0500000000",
    "email": "dana@example.com",
}


class Out(io.StringIO):
    def reconfigure(self, encoding=None):
        pass


def run(argv):
    out, err = Out(), Out()
    with patch.object(sys, "stdout", out), patch.object(sys, "stderr", err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class CliModuleTests(unittest.TestCase):
    def test_cars_file_plain_reports(self):
        code, out, _ = run([str(SAMPLE), "--city", "ירושלים"])
        self.assertEqual(code, 0)
        self.assertIn("חונה", out)
        self.assertIn("ירושלים", out)

    def test_cars_file_no_photo_and_name(self):
        code, out, _ = run(
            [str(SAMPLE), "--city", "ירושלים", "--name", "דנה", "--no-photo"]
        )
        self.assertEqual(code, 0)
        self.assertIn("דנה", out)
        self.assertNotIn("מצורפת תמונה", out)

    def test_municipality_text_and_json(self):
        with TemporaryDirectory() as tmp:
            reporter = Path(tmp) / "reporter.json"
            reporter.write_text(json.dumps(REPORTER), encoding="utf-8")
            code, out, _ = run(
                [
                    str(SAMPLE),
                    "--municipality",
                    "jerusalem",
                    "--reporter",
                    str(reporter),
                ]
            )
            self.assertEqual(code, 0)
            self.assertIn("# 1/", out)
            code, out, _ = run(
                [
                    str(SAMPLE),
                    "--municipality",
                    "jerusalem",
                    "--reporter",
                    str(reporter),
                    "--json",
                ]
            )
            self.assertEqual(code, 0)
            parsed = json.loads(out)
            self.assertIsInstance(parsed, list)
            self.assertTrue(parsed)

    def test_intake_fixture(self):
        code, out, _err = run(["--intake", str(INTAKE_FIXTURE), "--city", "ירושלים"])
        self.assertEqual(code, 0)
        self.assertIn("חונה", out)

    def test_missing_input_file_returns_1(self):
        code, _, err = run(["/nonexistent/cars.json"])
        self.assertEqual(code, 1)
        self.assertIn("error:", err)

    def test_invalid_json_returns_1(self):
        with TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("not json", encoding="utf-8")
            code, _, err = run([str(bad)])
            self.assertEqual(code, 1)
            self.assertIn("error:", err)

    def test_parser_errors(self):
        for argv in (
            [],
            [str(SAMPLE), "--intake", str(INTAKE_FIXTURE)],
            ["--drive-folder", "abc"],
            ["--drive-api", "http://x"],
            [str(SAMPLE), "--reporter", "r.json"],
            [str(SAMPLE), "--json"],
            ["--intake", str(INTAKE_FIXTURE), "--drive-api", "http://x"],
        ):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as ctx:
                cli.main(argv)
            self.assertEqual(ctx.exception.code, 2)

    def test_main_module_delegates(self):
        import runpy

        with (
            patch.object(sys, "exit") as mock_exit,
            patch.object(
                sys, "argv", ["parking_report", str(SAMPLE), "--city", "ירושלים"]
            ),
            patch.object(sys, "stdout", Out()),
        ):
            runpy.run_module("parking_report", run_name="__main__")
        mock_exit.assert_called_once_with(0)


if __name__ == "__main__":
    unittest.main()
