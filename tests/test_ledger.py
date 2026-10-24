"""Functional durable ledger tests use only temporary local Git repositories."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

try:
    from parking_report.ledger import GitLedger, LedgerError
    from parking_report.api106 import submit, ReviewNeeded
except ImportError:
    GitLedger = None


@unittest.skipIf(GitLedger is None, "Install api extra")
class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.remote = self.root / "remote.git"
        subprocess.run(
            ["git", "init", "--bare", str(self.remote)], capture_output=True, check=True
        )
        self.a = self.root / "a"
        self.clone(self.a)
        self.git(self.a, "checkout", "--orphan", "106-ledger")
        (self.a / "ledger.json").write_text(
            json.dumps({"version": 1, "reports": {}, "photos": {}})
        )
        self.git(self.a, "add", ".")
        self.git(self.a, "commit", "-m", "Initialize")
        self.git(self.a, "push", "origin", "HEAD:106-ledger")

    def git(self, root, *args):
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True
        ).stdout

    def clone(self, path):
        subprocess.run(
            ["git", "clone", str(self.remote), str(path)],
            capture_output=True,
            check=True,
        )
        self.git(path, "config", "user.name", "Test")
        self.git(path, "config", "user.email", "test@example.invalid")

    def test_reservation_id_and_photo_guard(self):
        with GitLedger(self.a).locked() as ledger:
            ledger.reserve("stable-id", "a" * 64, "b" * 64)
            ledger.reserve("stable-id", "a" * 64, "b" * 64)
            with self.assertRaises(LedgerError):
                ledger.reserve("stable-id", "c" * 64, "b" * 64)
            with self.assertRaises(LedgerError):
                ledger.reserve("renamed-id", "a" * 64, "b" * 64)
        b = self.root / "b"
        self.clone(b)
        self.git(b, "checkout", "106-ledger")
        with GitLedger(b).locked() as ledger:
            self.assertEqual(
                ledger.reserve("stable-id", "a" * 64, "b" * 64)["state"]["status"],
                "prepared",
            )

    def test_stale_writer_stops(self):
        b = self.root / "b"
        self.clone(b)
        self.git(b, "checkout", "106-ledger")
        with GitLedger(self.a).locked() as ledger:
            ledger.reserve("one", "a" * 64, "b" * 64)
        with self.assertRaises(LedgerError):
            with GitLedger(b).locked():
                pass

    def test_uncertain_creation_survives_fresh_runner(self):
        class Fake:
            def login(self):
                return "mock-token"

            def contact(self, *args):
                return {}

            def create(self, *args):
                raise OSError("timeout")

        with GitLedger(self.a).locked() as ledger:
            entry = ledger.reserve("one", "a" * 64, "b" * 64)
            with self.assertRaises(ReviewNeeded):
                submit(
                    Fake(),
                    {},
                    {},
                    b"jpeg",
                    "a" * 64,
                    self.root / "state",
                    initial_state=entry["state"],
                    checkpoint=lambda s: ledger.checkpoint("one", s),
                )
        b = self.root / "b"
        self.clone(b)
        self.git(b, "checkout", "106-ledger")
        with GitLedger(b).locked() as ledger:
            entry = ledger.reserve("one", "a" * 64, "b" * 64)
            self.assertEqual(entry["state"]["status"], "creating")
            with self.assertRaises(ReviewNeeded):
                submit(
                    Fake(),
                    {},
                    {},
                    b"jpeg",
                    "a" * 64,
                    self.root / "new-state",
                    initial_state=entry["state"],
                    checkpoint=lambda s: ledger.checkpoint("one", s),
                )

    def test_durable_receipt_no_network(self):
        class Fake:
            def login(self):
                return "mock-token"

            def contact(self, *args):
                return {}

            def create(self, *args):
                return {"CaseIdInCRM": "fake-case", "WebCaseIdInCRM": "fake-web"}

            def attach(self, *args):
                pass

        with GitLedger(self.a).locked() as ledger:
            entry = ledger.reserve("one", "a" * 64, "b" * 64)
            submit(
                Fake(),
                {},
                {},
                b"jpeg",
                "a" * 64,
                self.root / "state",
                initial_state=entry["state"],
                checkpoint=lambda s: ledger.checkpoint("one", s),
            )
        b = self.root / "b"
        self.clone(b)
        self.git(b, "checkout", "106-ledger")
        with GitLedger(b).locked() as ledger:
            entry = ledger.reserve("one", "a" * 64, "b" * 64)
            out = submit(
                object(),
                {},
                {},
                b"jpeg",
                "a" * 64,
                self.root / "new-state",
                initial_state=entry["state"],
                checkpoint=lambda s: ledger.checkpoint("one", s),
            )
            self.assertEqual(out["status"], "already_submitted")

    def test_failed_checkpoint_prevents_network(self):
        def fail(s):
            raise LedgerError("push failed")

        with self.assertRaises(LedgerError):
            submit(
                object(),
                {},
                {},
                b"jpeg",
                "a" * 64,
                self.root / "state",
                initial_state={"version": 1, "status": "prepared"},
                checkpoint=fail,
            )

    def test_rejected_push_fail_closed(self):
        hook = self.remote / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        with self.assertRaises(LedgerError):
            with GitLedger(self.a).locked() as ledger:
                ledger.reserve("one", "a" * 64, "b" * 64)
        with self.assertRaises(LedgerError):
            with GitLedger(self.a).locked():
                pass
