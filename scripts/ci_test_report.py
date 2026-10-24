"""Aggregate unittest metrics for CI job summaries. Never include failure bodies."""

import argparse
import ast
import html
import inspect
import json
import math
import os
import re
import sys
import textwrap
import unittest
from pathlib import Path


def safe_text(value):
    text = str(value).replace("\n", " ")[:500]
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email]", text)
    text = re.sub(r"\b[0-9][0-9 -]{5,}[0-9]\b", "[number]", text)
    text = re.sub(r"(?:[A-Za-z]:)?/[^ ]+", "[path]", text)
    text = re.sub(
        r"(?i)(token|password|secret|key)\s*[:=]\s*[^ ]+", r"\1=[redacted]", text
    )
    return html.escape(text).replace("|", "&#124;")


def safe_value(value):
    """No repr()/str() on arbitrary runtime objects; default deny values."""
    if value is None or type(value) is bool:
        return str(value)
    if type(value) is int and -10000 <= value <= 10000:
        return str(value)
    if type(value) is float and math.isfinite(value) and abs(value) <= 10000:
        return str(value)
    return "[" + type(value).__name__ + " value withheld]"


def source_plan(test):
    """Static structural plan, not an execution trace; never copy source literals."""
    method = getattr(test, getattr(test, "_testMethodName", ""), None)
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
    except (OSError, TypeError, IndentationError, SyntaxError):
        return []
    steps = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            function = node.func
            name = (
                function.attr
                if isinstance(function, ast.Attribute)
                else function.id
                if isinstance(function, ast.Name)
                else "call"
            )
            if name.startswith("_") or name in {"str", "repr", "print"}:
                continue
            inputs = [type(arg).__name__ for arg in node.args]
            steps.append({"line": node.lineno, "call": name, "input_shapes": inputs})
    return sorted(steps, key=lambda row: row["line"])[:80]


class AssertionObserver:
    """Wrap only this test instance; preserve unittest semantics and originals."""

    def __init__(self, test):
        self.test, self.events, self.saved, self.depth = test, [], {}, 0

    def install(self):
        for name in dir(unittest.TestCase):
            if not name.startswith("assert") or not callable(
                getattr(self.test, name, None)
            ):
                continue
            original = getattr(self.test, name)
            self.saved[name] = (
                name in self.test.__dict__,
                self.test.__dict__.get(name),
            )

            def wrapper(*args, _method=original, _name=name, **kwargs):
                outer = self.depth == 0
                self.depth += 1
                event = {
                    "assertion": _name,
                    "inputs": [safe_value(v) for v in args[:2]],
                    "outcome": "completed",
                }
                if (
                    _name
                    in {
                        "assertEqual",
                        "assertNotEqual",
                        "assertAlmostEqual",
                        "assertIs",
                        "assertIsNot",
                    }
                    and len(args) >= 2
                ):
                    event["actual"], event["expected"] = (
                        safe_value(args[0]),
                        safe_value(args[1]),
                    )
                elif (
                    _name
                    in {"assertTrue", "assertFalse", "assertIsNone", "assertIsNotNone"}
                    and args
                ):
                    event["actual"] = safe_value(args[0])
                    event["expected"] = {
                        "assertTrue": "truthy",
                        "assertFalse": "falsey",
                        "assertIsNone": "None",
                        "assertIsNotNone": "not None",
                    }[_name]
                try:
                    result = _method(*args, **kwargs)
                    if _name.startswith(
                        ("assertRaises", "assertWarns", "assertLogs", "assertNoLogs")
                    ):
                        event["outcome"] = (
                            "context or call returned; final verdict is test status"
                        )
                    return result
                except BaseException:
                    event["outcome"] = "raised; see final test status"
                    raise
                finally:
                    self.depth -= 1
                    if outer and len(self.events) < 100:
                        self.events.append(event)

            setattr(self.test, name, wrapper)

    def restore(self):
        for name, (existed, value) in self.saved.items():
            if existed:
                setattr(self.test, name, value)
            else:
                self.test.__dict__.pop(name, None)


def skip_explanation(reason):
    """Only grounded known routes; unknown skips get no invented alternate job."""
    if reason in {
        "Source-only workflows are excluded from the Docker test image",
        "Source-only workflow excluded from Docker test image",
    }:
        return {
            "why": "This Docker image omits .github workflow source files, so a source-file check cannot run inside it.",
            "where": "The same source check runs in the Python test and coverage jobs on the full repository checkout.",
        }
    if reason in {"Install api extra", "install the api extra"}:
        return {
            "why": "The optional API dependencies are not installed in this environment.",
            "where": "The hosted Python and coverage jobs install .[api] and run these tests there.",
        }
    return {
        "why": "This test declared a prerequisite was unavailable: "
        + html.unescape(safe_text(reason)),
        "where": "No alternate execution job is verified for this skip. Review the test prerequisite and job logs.",
    }


class DetailedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.details = []
        self.observers = {}

    def startTest(self, test):
        super().startTest(test)
        observer = AssertionObserver(test)
        self.observers[id(test)] = observer
        observer.install()

    def stopTest(self, test):
        observer = self.observers.pop(id(test), None)
        if observer:
            observer.restore()
        super().stopTest(test)

    def record(self, test, status, reason=""):
        # Subtest repr can embed private parameter values; keep only parent ID.
        parent = getattr(test, "test_case", test)
        self.details.append(
            {
                "test": html.unescape(safe_text(parent.id())),
                "status": status,
                "checks": html.unescape(
                    safe_text(parent.shortDescription() or parent.id().split(".")[-1])
                ),
                "reason": html.unescape(safe_text(reason)),
                "skip_context": skip_explanation(reason) if status == "skipped" else {},
                "plan": source_plan(parent),
                "assertions": list(self.observers[id(parent)].events)
                if id(parent) in self.observers
                else [],
            }
        )

    def addSuccess(self, test):
        super().addSuccess(test)
        self.record(test, "passed")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.record(test, "failed", err[0].__name__ + "; assertion details in job logs")

    def addError(self, test, err):
        super().addError(test, err)
        self.record(test, "error", err[0].__name__ + "; traceback in job logs")

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.record(test, "skipped", reason)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self.record(test, "expected failure", err[0].__name__)

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.record(test, "unexpected success", "Expected failure passed")

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self.record(
                test,
                "failed" if issubclass(err[0], test.failureException) else "error",
                err[0].__name__ + " in subtest; parameters omitted, see logs",
            )


def run_tests(start, output):
    suite = unittest.defaultTestLoader.discover(start)
    result = unittest.TextTestRunner(verbosity=2, resultclass=DetailedResult).run(suite)
    metrics = {
        "tests": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "expected_failures": len(result.expectedFailures),
        "unexpected_successes": len(result.unexpectedSuccesses),
    }
    metrics["passed"] = metrics["tests"] - sum(
        metrics[key]
        for key in (
            "failures",
            "errors",
            "skipped",
            "expected_failures",
            "unexpected_successes",
        )
    )
    metrics["success"] = result.wasSuccessful()
    metrics["details"] = result.details
    Path(output).write_text(json.dumps(metrics))
    return 0 if result.wasSuccessful() else 1


def summary(metrics_path, coverage_path=None):
    try:
        data = json.loads(Path(metrics_path).read_text())
        fields = [
            ("Tests run", "tests"),
            ("Passed", "passed"),
            ("Failures", "failures"),
            ("Errors", "errors"),
            ("Skipped", "skipped"),
            ("Expected failures", "expected_failures"),
            ("Unexpected successes", "unexpected_successes"),
        ]
        if any(type(data[key]) is not int or data[key] < 0 for _, key in fields):
            raise ValueError("Invalid metrics")
        if type(data["success"]) is not bool:
            raise ValueError("Invalid success flag")
        heading = "PASS" if data["success"] else "FAIL"
        lines = [
            "## Test results: " + heading,
            "",
            "| Metric | Count |",
            "| --- | ---: |",
        ]
        lines.extend(f"| {title} | {data[key]} |" for title, key in fields)
    except (OSError, ValueError, KeyError, TypeError):
        lines = [
            "## Test results: unavailable",
            "",
            "The test runner did not produce valid aggregate metrics. Check the job logs.",
        ]
    coverage = None
    if coverage_path:
        try:
            totals = json.loads(Path(coverage_path).read_text())["totals"]
            value = totals["percent_covered"]
            if type(value) in (int, float) and 0 <= value <= 100:
                coverage = f"{value:.2f}% (coverage.py statement/branch measurement)"
        except (OSError, ValueError, TypeError, KeyError):
            pass
    lines.extend(
        ["", "Coverage: " + (coverage or "not measured or unavailable in this job.")]
    )
    try:
        details = data.get("details", [])
        priority = {
            "failed": 0,
            "error": 1,
            "unexpected success": 2,
            "skipped": 3,
            "expected failure": 4,
            "passed": 5,
        }
        if details:
            lines.extend(
                [
                    "",
                    "<details>",
                    "<summary>Per-test results (failures and skips first)</summary>",
                    "",
                    "| Status | Test | What it checks | Reason |",
                    "| --- | --- | --- | --- |",
                ]
            )
            for row in sorted(
                details, key=lambda row: priority.get(row.get("status"), 9)
            ):
                # Data file is not an authority for HTML or unsafe runtime text.
                values = [
                    safe_text(row.get(key, ""))
                    for key in ("status", "test", "checks", "reason")
                ]
                lines.append("| " + " | ".join(values) + " |")
            lines.extend(
                [
                    "",
                    "Expand an individual test for its structural plan and observed assertions.",
                ]
            )
            for row in sorted(
                details, key=lambda row: priority.get(row.get("status"), 9)
            ):
                lines.extend(
                    [
                        "",
                        "<details>",
                        "<summary>"
                        + safe_text(row.get("status", ""))
                        + ": "
                        + safe_text(row.get("test", ""))
                        + "</summary>",
                        "",
                        "Source plan (static, branches may not execute; literal inputs withheld):",
                        "",
                    ]
                )
                if row.get("status") == "skipped":
                    context = row.get("skip_context", {})
                    lines.extend(
                        [
                            "Why skipped: "
                            + safe_text(context.get("why", "Not recorded.")),
                            "",
                            "Where it runs instead: "
                            + safe_text(context.get("where", "Not verified.")),
                            "",
                        ]
                    )
                for step in row.get("plan", []):
                    lines.append(
                        "- Line "
                        + safe_text(step.get("line"))
                        + ": `"
                        + safe_text(step.get("call"))
                        + "` ("
                        + safe_text(", ".join(step.get("input_shapes", [])))
                        + ")."
                    )
                lines.extend(
                    [
                        "",
                        "Observed assertions (runtime values are privacy-filtered):",
                        "",
                        "| Assertion | Inputs | Expected | Actual | Observed outcome |",
                        "| --- | --- | --- | --- | --- |",
                    ]
                )
                for event in row.get("assertions", []):
                    values = [
                        safe_text(event.get("assertion", "")),
                        safe_text(", ".join(event.get("inputs", []))),
                        safe_text(event.get("expected", "not captured")),
                        safe_text(event.get("actual", "not captured")),
                        safe_text(event.get("outcome", "")),
                    ]
                    lines.append("| " + " | ".join(values) + " |")
                if not row.get("assertions"):
                    lines.append(
                        "| None captured | | | | May use helpers, plain assert, skip or setup failure. See test status. |"
                    )
                lines.extend(
                    [
                        "",
                        "Strings, objects and large numbers are withheld. Context-manager assertions are not claimed passed until the test completes.",
                        "",
                        "</details>",
                    ]
                )
            lines.extend(["", "</details>"])
    except (UnboundLocalError, TypeError, AttributeError):
        lines.extend(["", "Per-test details unavailable."])
    lines.extend(
        [
            "",
            "Assertion bodies/tracebacks stay in job logs; source names and safe reasons are shown.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run")
    run.add_argument("--start", default="tests")
    run.add_argument("--output", default="test-results.json")
    render = sub.add_parser("summary")
    render.add_argument("--metrics", default="test-results.json")
    render.add_argument("--coverage", default=None)
    args = parser.parse_args(argv)
    if args.action == "run":
        return run_tests(args.start, args.output)
    text = summary(args.metrics, args.coverage)
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if destination:
        with open(destination, "a") as stream:
            stream.write(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
