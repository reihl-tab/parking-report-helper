"""Characterization of ci_test_report.summary() output (#288).

The expected Markdown under tests/fixtures/ci_summary_288/ was produced by
summary() before the #288 refactor and must stay byte-identical: CI job
summaries (.github/workflows/tests.yml calls `ci_test_report.py summary`)
are rendered from it. Synthetic inputs only.
"""

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci_test_report.py"
EXPECTED = Path(__file__).resolve().parent / "fixtures" / "ci_summary_288"
spec = importlib.util.spec_from_file_location("ci_test_report_288", SCRIPT)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

COUNTS = {
    "tests": 9,
    "passed": 3,
    "failures": 2,
    "errors": 1,
    "skipped": 1,
    "expected_failures": 1,
    "unexpected_successes": 1,
}

ROWS = [
    {
        "status": "passed",
        "test": "test_alpha.AlphaTests.test_ok",
        "checks": "Totals add up",
        "reason": "",
        "plan": [{"line": 12, "call": "assertEqual", "input_shapes": ["int", "int"]}],
        "assertions": [
            {
                "assertion": "assertEqual",
                "inputs": ["int", "int"],
                "expected": "4",
                "actual": "4",
                "outcome": "passed",
            }
        ],
    },
    {
        "status": "failed",
        "test": "test_beta.BetaTests.test_markup",
        "checks": "<b>Escapes</b> | pipes",
        "reason": "AssertionError for user@example.test at /tmp/x token=abc 1234 5678",
        "plan": [
            {"line": 3, "call": "assertIn", "input_shapes": []},
            {"line": 4, "call": "assertTrue"},
        ],
        "assertions": [
            {"assertion": "assertIn", "inputs": ["str"], "outcome": "failed"},
        ],
    },
    {
        "status": "skipped",
        "test": "test_gamma.GammaTests.test_skip_ctx",
        "checks": "Optional tool",
        "reason": "Optional tool not installed",
        "skip_context": {"why": "No tool here", "where": "Docker image"},
    },
    {
        "status": "skipped",
        "test": "test_gamma.GammaTests.test_skip_bare",
        "checks": "",
        "reason": "bare",
    },
    {"status": "error", "test": "test_delta.D.test_err", "reason": "ValueError"},
    {"status": "expected failure", "test": "test_eps.E.test_xf"},
    {"status": "unexpected success", "test": "test_eps.E.test_us"},
    {"status": "mystery", "test": "test_zeta.Z.test_unknown_status"},
    {"test": "test_eta.H.test_no_status", "checks": "multi\nline"},
]


def metrics(**extra):
    return {**COUNTS, "success": False, "details": ROWS, **extra}


# case name -> (metrics file content or None for a missing file,
#               coverage file content, None for no --coverage, or MISSING)
MISSING = object()
CASES = {
    "full": (
        json.dumps(metrics()),
        json.dumps({"totals": {"percent_covered": 81.125}}),
    ),
    "full_no_coverage": (json.dumps(metrics()), None),
    "pass_no_details": (
        json.dumps({**COUNTS, "success": True, "details": []}),
        None,
    ),
    "pass_no_details_key": (json.dumps({**COUNTS, "success": True}), MISSING),
    "missing_metrics": (None, None),
    "corrupt_metrics": ("{broken", None),
    "list_metrics": ("[1, 2]", None),
    "string_metrics": ('"text"', None),
    "negative_count": (json.dumps(metrics(errors=-1)), None),
    "bool_count": (json.dumps(metrics(tests=True)), None),
    "float_count": (json.dumps(metrics(tests=9.0)), None),
    "success_not_bool": (json.dumps(metrics(success=1)), None),
    "missing_key_with_details": (
        json.dumps({k: v for k, v in metrics().items() if k != "skipped"}),
        None,
    ),
    "details_null": (json.dumps(metrics(details=None)), None),
    "details_row_not_object": (json.dumps(metrics(details=["row"])), None),
    "details_mapping": (json.dumps(metrics(details={"a": 1})), None),
    "details_number": (json.dumps(metrics(details=7)), None),
    "status_unhashable": (
        json.dumps(metrics(details=[{"status": ["x"], "test": "t"}])),
        None,
    ),
    "plan_step_not_object": (
        json.dumps(
            metrics(
                details=[
                    {
                        "status": "failed",
                        "test": "t",
                        "plan": [{"line": 1, "call": "f"}, "bad", {"line": 3}],
                    }
                ]
            )
        ),
        None,
    ),
    "plan_shapes_not_strings": (
        json.dumps(
            metrics(
                details=[
                    {"status": "failed", "test": "t", "plan": [{"input_shapes": [1]}]}
                ]
            )
        ),
        None,
    ),
    "plan_not_list": (
        json.dumps(metrics(details=[{"status": "passed", "test": "t", "plan": 5}])),
        None,
    ),
    "skip_context_not_object": (
        json.dumps(
            metrics(details=[{"status": "skipped", "test": "t", "skip_context": "x"}])
        ),
        None,
    ),
    "assertion_not_object": (
        json.dumps(
            metrics(
                details=[
                    {
                        "status": "failed",
                        "test": "t",
                        "assertions": [{"assertion": "a"}, 3],
                    },
                    {"status": "passed", "test": "u"},
                ]
            )
        ),
        None,
    ),
    "assertion_inputs_not_strings": (
        json.dumps(
            metrics(
                details=[
                    {"status": "failed", "test": "t", "assertions": [{"inputs": [1]}]}
                ]
            )
        ),
        None,
    ),
    "second_row_breaks": (
        json.dumps(
            metrics(
                details=[
                    {"status": "failed", "test": "first"},
                    {"status": "passed", "test": "second", "plan": [None]},
                ]
            )
        ),
        None,
    ),
    "coverage_100": (
        json.dumps(metrics(details=[])),
        json.dumps({"totals": {"percent_covered": 100}}),
    ),
    "coverage_zero": (
        json.dumps(metrics(details=[])),
        json.dumps({"totals": {"percent_covered": 0.0}}),
    ),
    "coverage_over": (
        json.dumps(metrics(details=[])),
        json.dumps({"totals": {"percent_covered": 100.5}}),
    ),
    "coverage_negative": (
        json.dumps(metrics(details=[])),
        json.dumps({"totals": {"percent_covered": -1}}),
    ),
    "coverage_bool": (
        json.dumps(metrics(details=[])),
        json.dumps({"totals": {"percent_covered": True}}),
    ),
    "coverage_string": (
        json.dumps(metrics(details=[])),
        json.dumps({"totals": {"percent_covered": "50"}}),
    ),
    "coverage_nan": (
        json.dumps(metrics(details=[])),
        '{"totals": {"percent_covered": NaN}}',
    ),
    "coverage_no_totals": (json.dumps(metrics(details=[])), json.dumps({"other": 1})),
    "coverage_totals_list": (
        json.dumps(metrics(details=[])),
        json.dumps({"totals": [1]}),
    ),
    "coverage_corrupt": (json.dumps(metrics(details=[])), "{nope"),
    "coverage_missing_file": (json.dumps(metrics(details=[])), MISSING),
    "coverage_empty_path": (json.dumps(metrics(details=[])), ""),
}


def materialize(root, metrics_text, coverage):
    """Write one case's files; return (metrics_path, coverage_arg)."""
    metrics_path = root / "test-results.json"
    if metrics_text is not None:
        metrics_path.write_text(metrics_text)
    if coverage is MISSING:
        return metrics_path, str(root / "absent-coverage.json")
    if coverage is None or coverage == "":
        return metrics_path, (None if coverage is None else "")
    coverage_path = root / "coverage.json"
    coverage_path.write_text(coverage)
    return metrics_path, str(coverage_path)


def render(name):
    metrics_text, coverage = CASES[name]
    with tempfile.TemporaryDirectory() as tmp:
        metrics_path, coverage_arg = materialize(Path(tmp), metrics_text, coverage)
        return c.summary(metrics_path, coverage_arg)


def expected(name):
    return (EXPECTED / f"{name}.md").read_bytes().decode("utf-8")


class SummaryCharacterizationTests(unittest.TestCase):
    def test_every_case_has_expected_output_and_no_strays(self):
        self.assertEqual(sorted(p.stem for p in EXPECTED.glob("*.md")), sorted(CASES))

    def test_summary_output_is_byte_identical(self):
        for name in CASES:
            with self.subTest(case=name):
                self.assertEqual(render(name), expected(name))

    def test_cli_prints_summary_with_trailing_newline(self):
        for name in ("full", "corrupt_metrics", "coverage_100"):
            metrics_text, coverage = CASES[name]
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                metrics_path, coverage_arg = materialize(
                    Path(tmp), metrics_text, coverage
                )
                argv = ["summary", "--metrics", str(metrics_path)]
                if coverage_arg is not None:
                    argv += ["--coverage", coverage_arg]
                out = io.StringIO()
                env = {
                    k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"
                }
                with (
                    patch.dict(os.environ, env, clear=True),
                    contextlib.redirect_stdout(out),
                ):
                    self.assertEqual(c.main(argv), 0)
                self.assertEqual(out.getvalue(), expected(name) + "\n")

    def test_cli_appends_to_step_summary_without_newline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metrics_path, coverage_arg = materialize(root, *CASES["full"])
            target = root / "step-summary.md"
            target.write_text("Earlier\n")
            out = io.StringIO()
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(target)}):
                with contextlib.redirect_stdout(out):
                    code = c.main(
                        [
                            "summary",
                            "--metrics",
                            str(metrics_path),
                            "--coverage",
                            coverage_arg,
                        ]
                    )
            self.assertEqual(code, 0)
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(target.read_text(), "Earlier\n" + expected("full"))

    def test_script_invoked_as_the_workflow_calls_it(self):
        # tests.yml: python scripts/ci_test_report.py summary --metrics test-results.json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            materialize(root, *CASES["full"])
            env = {k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"}
            for args, name in (
                ([], "full_no_coverage"),
                (["--coverage", "coverage.json"], "full"),
            ):
                with self.subTest(args=args):
                    done = subprocess.run(
                        [
                            sys.executable,
                            str(SCRIPT),
                            "summary",
                            "--metrics",
                            "test-results.json",
                            *args,
                        ],
                        cwd=root,
                        env=env,
                        capture_output=True,
                        timeout=60,
                    )
                    self.assertEqual(done.returncode, 0)
                    self.assertEqual(done.stderr, b"")
                    self.assertEqual(done.stdout.decode("utf-8"), expected(name) + "\n")


if __name__ == "__main__":
    unittest.main()
