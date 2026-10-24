"""Synthetic runner tests; never execute real integration effects."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci_test_report.py"
spec = importlib.util.spec_from_file_location("ci_test_report", SCRIPT)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class CITestReportTests(unittest.TestCase):
    def test_functional_counts_and_failing_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "test_synthetic.py").write_text("""import unittest
class Synthetic(unittest.TestCase):
 def test_pass(self): pass
 def test_fail(self): self.fail('PRIVATE_MARKER')
 def test_error(self): raise ValueError('PRIVATE_MARKER')
 @unittest.skip('Optional backend unavailable')
 def test_skip(self): pass
 @unittest.expectedFailure
 def test_expected(self): self.fail('PRIVATE_MARKER')
 @unittest.expectedFailure
 def test_unexpected(self): pass
""")
            output = root / "metrics.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "run",
                    "--start",
                    tmp,
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 1)
            metrics = json.loads(output.read_text())
            details = metrics.pop("details")
            self.assertEqual(len(details), 6)
            self.assertEqual(
                metrics,
                {
                    "tests": 6,
                    "passed": 1,
                    "failures": 1,
                    "errors": 1,
                    "skipped": 1,
                    "expected_failures": 1,
                    "unexpected_successes": 1,
                    "success": False,
                },
            )
            text = c.summary(output)
            self.assertIn("FAIL", text)
            self.assertNotIn("PRIVATE_MARKER", text)
            self.assertIn("test_synthetic", text)
            self.assertIn("Per-test results", text)
            self.assertLess(text.index("| failed |"), text.index("| passed |"))

    def test_success_and_import_error_counts(self):
        for source, success in [
            (
                "import unittest\nclass T(unittest.TestCase):\n def test_ok(self): pass\n",
                True,
            ),
            ('raise ImportError("private")', False),
        ]:
            with self.subTest(success=success), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "test_sample.py").write_text(source)
                output = root / "metrics.json"
                result = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPT),
                        "run",
                        "--start",
                        tmp,
                        "--output",
                        str(output),
                    ],
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0 if success else 1)
                data = json.loads(output.read_text())
                self.assertEqual(data["tests"], 1)
                self.assertEqual(data["success"], success)

    def test_coverage_and_append_summary_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metrics = root / "metrics.json"
            metrics.write_text(
                json.dumps(
                    {
                        "tests": 2,
                        "passed": 2,
                        "failures": 0,
                        "errors": 0,
                        "skipped": 0,
                        "expected_failures": 0,
                        "unexpected_successes": 0,
                        "success": True,
                    }
                )
            )
            coverage = root / "coverage.json"
            coverage.write_text(
                json.dumps(
                    {
                        "totals": {"percent_covered": 81.125},
                        "files": {"private.py": "never include"},
                    }
                )
            )
            output = root / "summary.md"
            output.write_text("Earlier summary\n")
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(output)}):
                self.assertEqual(
                    c.main(
                        [
                            "summary",
                            "--metrics",
                            str(metrics),
                            "--coverage",
                            str(coverage),
                        ]
                    ),
                    0,
                )
            text = output.read_text()
            self.assertIn("Earlier summary", text)
            self.assertIn("81.12%", text)
            self.assertIn("PASS", text)
            self.assertNotIn("private.py", text)

    def test_missing_corrupt_or_non_numeric_input_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metrics.json"
            self.assertIn("unavailable", c.summary(path))
            for content in ["{broken", "{}", json.dumps({"tests": "secret"})]:
                path.write_text(content)
                self.assertIn("unavailable", c.summary(path))
                self.assertNotIn("secret", c.summary(path))

    def test_per_test_reasons_redacted_and_markup_escaped(self):
        text = c.safe_text(
            "<script>alert(1)</script> plate 12345678 user@example.test token=abc123 /private/photo.jpg"
        )
        self.assertNotIn("<script>", text)
        self.assertNotIn("12345678", text)
        self.assertNotIn("user@example.test", text)
        self.assertNotIn("abc123", text)
        self.assertNotIn("/private/photo.jpg", text)
        self.assertEqual(
            c.safe_text("Optional GPU not available"), "Optional GPU not available"
        )

    def test_workflow_always_publishes_summary_and_preserves_coverage_failure(self):
        path = ROOT / ".github/workflows/tests.yml"
        if not path.exists():
            self.skipTest("Source-only workflow excluded from Docker test image")
        text = path.read_text()
        self.assertIn("scripts/ci_test_report.py run", text)
        self.assertGreaterEqual(text.count("if: ${{ always() }}"), 2)
        self.assertIn("--coverage coverage.json", text)
        self.assertIn('exit "$status"', text)
        self.assertNotIn("continue-on-error: true", text)


if __name__ == "__main__":
    unittest.main()
