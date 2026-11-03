"""CLI bound validation for scripts/fuzz_parsers.py (#246).

``--seconds`` and ``--iterations`` are mutually exclusive, and
``--iterations`` must be >= 1. Rejected invocations are argparse usage
errors (exit 2) that stop before any fuzzing runs; accepted invocations keep
their pre-#246 behaviour. The fuzz loop is patched out for the accepted
cases except one real 1-iteration run, so nothing here fuzzes for long.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fuzz_parsers  # noqa: E402

ALL_TARGETS = sorted(fuzz_parsers.TARGETS)


class FuzzCliBoundsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.corpus = Path(self._tmp.name) / "corpus"

    def _run(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        argv = [*args, "--corpus-dir", str(self.corpus)]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = fuzz_parsers.main(argv)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
        return code, out.getvalue(), err.getvalue()

    def _assert_usage_error(self, args: tuple[str, ...], expected: str) -> None:
        with mock.patch.object(fuzz_parsers, "fuzz") as fake_fuzz:
            code, out, err = self._run(*args)
        self.assertEqual(code, 2, err)
        self.assertIn(expected, err)
        self.assertIn("usage:", err)
        self.assertNotIn("Traceback", err)
        # No local paths in the message (the corpus dir is a temp path).
        self.assertNotIn(self._tmp.name, err)
        self.assertEqual(out, "")
        fake_fuzz.assert_not_called()
        self.assertFalse(self.corpus.exists())

    def test_seconds_with_iterations_is_rejected(self):
        cases = {
            ("--seconds", "5", "--iterations", "3"): (
                "argument --iterations: not allowed with argument --seconds"
            ),
            ("--iterations", "3", "--seconds", "5"): (
                "argument --seconds: not allowed with argument --iterations"
            ),
            ("--iterations=1", "--seconds=30", "--target", "parse-datetime"): (
                "argument --seconds: not allowed with argument --iterations"
            ),
        }
        for args, expected in cases.items():
            with self.subTest(args=args):
                self._assert_usage_error(args, expected)

    def test_iterations_below_one_is_rejected(self):
        for value in ("0", "-1", "-200"):
            with self.subTest(iterations=value):
                self._assert_usage_error(
                    ("--iterations", value),
                    f"argument --iterations: must be >= 1, got {int(value)}",
                )

    def test_iterations_not_an_int_is_rejected(self):
        self._assert_usage_error(
            ("--iterations", "abc"),
            "argument --iterations: invalid int value: 'abc'",
        )

    def test_iterations_one_runs_exactly_one_iteration(self):
        code, out, err = self._run("--iterations", "1", "--target", "parse-datetime")
        self.assertEqual(code, 0, err)
        self.assertEqual(
            out, "fuzz[parse-datetime]: 1 iterations, 0 crash(es) (seed=180)\n"
        )
        self.assertEqual(err, "")

    def test_iterations_alone_passes_count_and_no_wall_clock(self):
        with mock.patch.object(fuzz_parsers, "fuzz", return_value=0) as fake:
            code, _, err = self._run("--iterations", "7")
        self.assertEqual(code, 0, err)
        self.assertEqual([c.args[0] for c in fake.call_args_list], ALL_TARGETS)
        for call in fake.call_args_list:
            self.assertIsNone(call.kwargs["seconds"])
            self.assertEqual(call.kwargs["iterations"], 7)

    def test_seconds_alone_keeps_wall_clock_bound(self):
        with mock.patch.object(fuzz_parsers, "fuzz", return_value=0) as fake:
            code, _, err = self._run("--seconds", "2.5", "--target", "stats-export")
        self.assertEqual(code, 0, err)
        fake.assert_called_once()
        self.assertEqual(fake.call_args.args, ("stats-export",))
        self.assertEqual(fake.call_args.kwargs["seconds"], 2.5)
        self.assertIsNone(fake.call_args.kwargs["iterations"])

    def test_neither_flag_keeps_30_second_default(self):
        with mock.patch.object(fuzz_parsers, "fuzz", return_value=0) as fake:
            code, _, err = self._run()
        self.assertEqual(code, 0, err)
        self.assertEqual([c.args[0] for c in fake.call_args_list], ALL_TARGETS)
        for call in fake.call_args_list:
            self.assertEqual(call.kwargs["seconds"], 30.0)
            self.assertIsNone(call.kwargs["iterations"])
            self.assertEqual(call.kwargs["seed"], 180)

    def test_help_documents_the_exclusive_bounds(self):
        out = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"COLUMNS": "100"}),
            contextlib.redirect_stdout(out),
            self.assertRaises(SystemExit) as cm,
        ):
            fuzz_parsers.main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        text = " ".join(out.getvalue().split())
        self.assertIn("[--seconds SECONDS | --iterations ITERATIONS]", text)
        self.assertIn("cannot be combined with --seconds", text)


if __name__ == "__main__":
    unittest.main()
