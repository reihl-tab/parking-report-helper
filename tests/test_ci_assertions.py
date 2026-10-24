import importlib.util
from pathlib import Path
import tempfile
import json
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci_test_report.py"
spec = importlib.util.spec_from_file_location("assertion_report", SCRIPT)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class AssertionDetailTests(unittest.TestCase):
    def test_observed_inputs_expected_actual_and_failure_without_secret_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "test_observed.py").write_text("""import unittest
class T(unittest.TestCase):
 def test_pass(self):
  self.assertEqual(2+2,4)
  self.assertTrue(True)
  self.assertEqual('SECRET_EMAIL_person@example.test','SECRET_EMAIL_person@example.test')
 def test_fail(self): self.assertEqual(3,4)
 def test_context(self):
  with self.assertRaises(ValueError): raise ValueError('SECRET_RUNTIME')
""")
            output = p / "results.json"
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
            self.assertEqual(result.returncode, 1)
            data = json.loads(output.read_text())
            passing = next(
                r for r in data["details"] if r["test"].endswith("test_pass")
            )
            self.assertEqual(passing["assertions"][0]["actual"], "4")
            self.assertEqual(passing["assertions"][0]["expected"], "4")
            self.assertEqual(passing["assertions"][0]["outcome"], "completed")
            failing = next(
                r for r in data["details"] if r["test"].endswith("test_fail")
            )
            self.assertEqual(failing["assertions"][0]["actual"], "3")
            self.assertEqual(failing["assertions"][0]["expected"], "4")
            self.assertIn("raised", failing["assertions"][0]["outcome"])
            text = c.summary(output)
            self.assertNotIn("SECRET_EMAIL", output.read_text())
            self.assertNotIn("SECRET_RUNTIME", text)
            self.assertIn("static, branches may not execute", text)
            self.assertEqual(text.count("<details>"), 4)
            self.assertNotIn("<details open", text)
            self.assertIn("context or call returned", text)

    def test_observer_restores_and_does_not_duplicate_internal_assertions(self):
        test = unittest.TestCase()
        original = test.assertEqual
        observer = c.AssertionObserver(test)
        observer.install()
        test.assertEqual("synthetic", "synthetic")
        observer.restore()
        self.assertEqual(test.assertEqual, original)
        self.assertEqual(len(observer.events), 1)
        self.assertEqual(observer.events[0]["inputs"], ["[str value withheld]"] * 2)

    def test_arbitrary_objects_not_stringified_or_secret_numbers_revealed(self):
        class Sensitive:
            def __repr__(self):
                raise AssertionError("do not inspect")

            def __str__(self):
                raise AssertionError("do not inspect")

        self.assertIn("withheld", c.safe_value(Sensitive()))
        self.assertIn("withheld", c.safe_value(12345678))
        self.assertIn("withheld", c.safe_value(float("nan")))
        self.assertEqual(c.safe_value(None), "None")
        self.assertEqual(c.safe_value(False), "False")

    def test_skip_explanation_grounded_routes_and_unknown_not_invented(self):
        context = c.skip_explanation(
            "Source-only workflow excluded from Docker test image"
        )
        self.assertIn("omits .github", context["why"])
        self.assertIn("Python test and coverage jobs", context["where"])
        self.assertIn(".[api]", c.skip_explanation("Install api extra")["where"])
        unknown = c.skip_explanation("Optional GPU backend unavailable")
        self.assertIn("No alternate execution job is verified", unknown["where"])
        self.assertNotIn("Playwright", unknown["where"])

    def test_static_plan_omits_literals_and_reports_call_shapes(self):
        plan = c.source_plan(self)
        self.assertTrue(plan)
        self.assertTrue(
            all(set(step) == {"line", "call", "input_shapes"} for step in plan)
        )
        self.assertNotIn("fixture_secret_literal", str(plan))
        self.assertEqual("fixture_secret_literal", "fixture_secret_literal")


if __name__ == "__main__":
    unittest.main()
