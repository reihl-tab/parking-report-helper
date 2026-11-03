"""Range checks for the fuzz bounds in scripts/fuzz_parsers.py (#262).

CLI: ``--seconds`` takes a finite number > 0. Zero, negatives, ``nan`` and
``inf`` are argparse usage errors (exit 2) that stop before any fuzzing
runs; before #262 they ran 0 iterations (or, for ``inf``, never stopped on
the clock) and exited 0.

Library: ``fuzz()`` itself raises ``ValueError`` for ``iterations < 1`` and
for a non-finite or non-positive ``seconds``, so code callers get the same
contract as the CLI instead of a silent 0-iteration "success". Nothing here
fuzzes for long: rejected cases never reach the loop and accepted CLI cases
run with ``fuzz()`` patched out.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fuzz_parsers  # noqa: E402


class FuzzSecondsCliTest(unittest.TestCase):
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

    def test_seconds_out_of_range_is_a_usage_error(self):
        cases = {
            ("--seconds", "0"): "must be finite and > 0, got 0.0",
            ("--seconds", "-1"): "must be finite and > 0, got -1.0",
            ("--seconds=-0.5",): "must be finite and > 0, got -0.5",
            ("--seconds", "nan"): "must be finite and > 0, got nan",
            ("--seconds", "inf"): "must be finite and > 0, got inf",
            ("--seconds=-inf",): "must be finite and > 0, got -inf",
            ("--seconds", "1e999"): "must be finite and > 0, got inf",
        }
        for args, expected in cases.items():
            with self.subTest(args=args):
                with mock.patch.object(fuzz_parsers, "fuzz") as fake_fuzz:
                    code, out, err = self._run(*args, "--target", "parse-datetime")
                self.assertEqual(code, 2, err)
                self.assertIn("usage:", err)
                self.assertIn(f"argument --seconds: {expected}", err)
                self.assertNotIn("Traceback", err)
                # No local paths in the message (the corpus dir is a temp path).
                self.assertNotIn(self._tmp.name, err)
                self.assertEqual(out, "")
                fake_fuzz.assert_not_called()
                self.assertFalse(self.corpus.exists())

    def test_seconds_not_a_number_keeps_argparse_wording(self):
        with mock.patch.object(fuzz_parsers, "fuzz") as fake_fuzz:
            code, out, err = self._run("--seconds", "abc")
        self.assertEqual(code, 2, err)
        self.assertIn("argument --seconds: invalid float value: 'abc'", err)
        self.assertEqual(out, "")
        fake_fuzz.assert_not_called()

    def test_positive_seconds_are_accepted(self):
        for value, expected in (("0.5", 0.5), ("1", 1.0), ("1e-3", 0.001)):
            with self.subTest(seconds=value):
                with mock.patch.object(fuzz_parsers, "fuzz", return_value=0) as fake:
                    code, _, err = self._run(
                        "--seconds", value, "--target", "cars-json"
                    )
                self.assertEqual(code, 0, err)
                fake.assert_called_once()
                self.assertEqual(fake.call_args.args, ("cars-json",))
                self.assertEqual(fake.call_args.kwargs["seconds"], expected)
                self.assertIsNone(fake.call_args.kwargs["iterations"])

    def test_seconds_still_exclusive_with_iterations(self):
        # The #246 group still wins over the range check for a valid value.
        with mock.patch.object(fuzz_parsers, "fuzz") as fake_fuzz:
            code, _, err = self._run("--seconds", "1", "--iterations", "2")
        self.assertEqual(code, 2, err)
        self.assertIn("not allowed with argument --seconds", err)
        fake_fuzz.assert_not_called()


class FuzzLibraryBoundsTest(unittest.TestCase):
    """``fuzz()`` validates its own bounds before touching the target."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.corpus = Path(self._tmp.name) / "corpus"

    def _fuzz(self, **bounds: object) -> int:
        return fuzz_parsers.fuzz(
            "parse-datetime",
            seed=180,
            corpus_dir=self.corpus,
            **bounds,
        )

    def _assert_rejected(self, bounds: dict[str, object], expected: str) -> None:
        out = io.StringIO()
        with (
            mock.patch.object(fuzz_parsers, "run_target") as fake_run,
            contextlib.redirect_stdout(out),
            self.assertRaises(ValueError) as cm,
        ):
            self._fuzz(**bounds)
        self.assertEqual(str(cm.exception), expected)
        fake_run.assert_not_called()
        self.assertEqual(out.getvalue(), "")
        self.assertFalse(self.corpus.exists())

    def test_iterations_below_one_raises(self):
        for value in (0, -1, -200):
            with self.subTest(iterations=value):
                self._assert_rejected(
                    {"iterations": value}, f"iterations must be >= 1, got {value}"
                )

    def test_bad_seconds_raises(self):
        for value in (0.0, -1.0, float("nan"), float("inf"), float("-inf")):
            with self.subTest(seconds=value):
                self._assert_rejected(
                    {"seconds": value}, f"seconds must be finite and > 0, got {value}"
                )

    def test_each_bound_is_checked_when_both_are_given(self):
        self._assert_rejected(
            {"seconds": 5.0, "iterations": 0}, "iterations must be >= 1, got 0"
        )
        self._assert_rejected(
            {"seconds": float("nan"), "iterations": 3},
            "seconds must be finite and > 0, got nan",
        )

    def test_no_bound_still_raises(self):
        self._assert_rejected({}, "pass seconds or iterations")

    def test_iterations_one_runs_one_iteration(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self._fuzz(iterations=1), 0)
        self.assertEqual(
            out.getvalue(),
            "fuzz[parse-datetime]: 1 iterations, 0 crash(es) (seed=180)\n",
        )

    def test_both_bounds_keep_first_reached_semantics(self):
        # Unchanged: with both bounds the loop stops at whichever comes first.
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self._fuzz(seconds=60.0, iterations=2), 0)
        self.assertIn(": 2 iterations,", out.getvalue())


if __name__ == "__main__":
    unittest.main()
