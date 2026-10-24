"""End-to-end tests of the real CLI using isolated temporary files."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CAR = {"plate": "00-000-00", "street": "Example 1", "datetime": "2026-01-02 03:04"}


class TestCLI(unittest.TestCase):
    def run_cli(self, content, *options):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(content, encoding="utf-8")
            return subprocess.run(
                [sys.executable, "-m", "parking_report", str(path), *options],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )

    def test_success_with_options(self):
        result = self.run_cli(
            json.dumps([CAR]),
            "--city",
            "Example City",
            "--name",
            "Example Reporter",
            "--no-photo",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertIn("Example City", result.stdout)
        self.assertIn("Example Reporter", result.stdout)
        self.assertNotIn("מצורפת תמונה", result.stdout)

    def test_multiple_reports_separated(self):
        result = self.run_cli(json.dumps([CAR, {**CAR, "plate": "11-111-11"}]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.strip().split("\n\n")), 2)

    def test_empty_list(self):
        result = self.run_cli("[]")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_invalid_inputs_fail_cleanly(self):
        for content in (
            "{bad json",
            "{}",
            "[null]",
            '[{"plate": null}]',
            json.dumps([CAR, {**CAR, "datetime": "invalid"}]),
        ):
            with self.subTest(content=content):
                result = self.run_cli(content)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertIn("error:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_missing_file_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "parking_report",
                    str(Path(directory) / "missing.json"),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn("error:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "parking_report", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--no-photo", result.stdout)


if __name__ == "__main__":
    unittest.main()
