"""Explicit subprocess failure handling (issue #116).

Both production `subprocess.run` sites (the private ledger's Git helper and
the 106 state persistence) use check=False and inspect the return code. A git
that cannot start (OSError) or a SubprocessError must map to the same domain
error (LedgerError / ReviewNeeded) as a nonzero exit, never escape raw, and
never echo exception text. Synthetic only: no network, no real ledger.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_api_cli_main import make_folder, run  # type: ignore[import-not-found]

from parking_report import api106
from parking_report.ledger import GitLedger, LedgerError

SECRET = "ZZ-DO-NOT-ECHO"
FAILURES = {
    "nonzero": SimpleNamespace(returncode=128, stdout=SECRET, stderr=SECRET),
    "oserror": FileNotFoundError(2, SECRET),
    "timeout": subprocess.TimeoutExpired(["git"], 1, output=SECRET),
    "subprocess_error": subprocess.SubprocessError(SECRET),
}


def fake_run(outcome):
    def run_(*args, **kwargs):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return run_


class LedgerGitTests(unittest.TestCase):
    def test_success_returns_stripped_stdout_with_check_false(self):
        calls = []

        def run_(*args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(returncode=0, stdout="abc123\n", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            ledger = GitLedger(Path(tmp))
            with patch("parking_report.ledger.subprocess.run", run_):
                self.assertEqual(ledger.git("rev-parse", "HEAD"), "abc123")
        self.assertEqual(calls[0][0][0], ["git", "rev-parse", "HEAD"])
        self.assertIs(calls[0][1]["check"], False)

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_real_git_success_and_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            ledger = GitLedger(Path(tmp))
            self.assertEqual(ledger.git("rev-parse", "--is-inside-work-tree"), "true")
            with self.assertRaises(LedgerError):
                ledger.git("rev-parse", "HEAD")  # no commits yet: nonzero exit

    def test_every_failure_maps_to_ledger_error_without_echo(self):
        for name, outcome in FAILURES.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                ledger = GitLedger(Path(tmp))
                with (
                    patch("parking_report.ledger.subprocess.run", fake_run(outcome)),
                    self.assertRaises(LedgerError) as ctx,
                ):
                    ledger.git("status")
                self.assertNotIn(SECRET, str(ctx.exception))
                # No chained raw exception for a traceback to print.
                self.assertIsNone(ctx.exception.__cause__)
                self.assertTrue(
                    ctx.exception.__context__ is None
                    or ctx.exception.__suppress_context__
                )


class StatePersistenceTests(unittest.TestCase):
    class Fake:
        def __init__(self):
            self.created = False

        def login(self):
            return "fake-token"

        def contact(self, *args):
            return {}

        def create(self, *args):
            self.created = True
            return {"CaseIdInCRM": "c", "WebCaseIdInCRM": "w"}

        def attach(self, *args):
            return {}

    def submit(self, run_):
        api = self.Fake()
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {"PARKING_STATE_GIT": "1"}),
            patch.object(api106.subprocess, "run", run_),
        ):
            result = api106.submit(api, {}, {}, b"jpeg", "d" * 64, Path(tmp))
        return api, result

    def test_success_runs_add_commit_push_with_check_false(self):
        calls = []

        def run_(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(returncode=0)

        api, result = self.submit(run_)
        self.assertTrue(api.created)
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(
            [c[0][:2] for c in calls[:3]],
            [["git", "add"], ["git", "commit"], ["git", "push"]],
        )
        self.assertTrue(all(kw["check"] is False for _, kw in calls))

    def test_failure_on_push_stops_before_create(self):
        def run_(command, **kwargs):
            return SimpleNamespace(returncode=1 if command[1] == "push" else 0)

        api = self.Fake()
        with (
            self.assertRaises(api106.ReviewNeeded),
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {"PARKING_STATE_GIT": "1"}),
            patch.object(api106.subprocess, "run", run_),
        ):
            api106.submit(api, {}, {}, b"jpeg", "d" * 64, Path(tmp))
        self.assertFalse(api.created)

    def test_every_failure_maps_to_review_needed_before_create(self):
        for name, outcome in FAILURES.items():
            with self.subTest(name):
                api = self.Fake()
                with (
                    self.assertRaises(api106.ReviewNeeded) as ctx,
                    tempfile.TemporaryDirectory() as tmp,
                    patch.dict(os.environ, {"PARKING_STATE_GIT": "1"}),
                    patch.object(api106.subprocess, "run", fake_run(outcome)),
                ):
                    api106.submit(api, {}, {}, b"jpeg", "d" * 64, Path(tmp))
                self.assertFalse(api.created)
                self.assertNotIn(SECRET, str(ctx.exception))


class CliExitUnchangedTests(unittest.TestCase):
    def test_ledger_git_that_cannot_start_still_exits_4_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, addresses = make_folder(tmp)
            ledger_dir = Path(tmp) / "ledger"
            ledger_dir.mkdir()
            with patch(
                "parking_report.ledger.subprocess.run",
                fake_run(FileNotFoundError(2, SECRET)),
            ):
                code, out = run(
                    [
                        str(folder),
                        "--addresses",
                        str(addresses),
                        "--ledger-dir",
                        str(ledger_dir),
                    ]
                )
        self.assertEqual(code, 4)
        self.assertIn('"status":"blocked"', out)
        self.assertNotIn(SECRET, out)


if __name__ == "__main__":
    unittest.main()
