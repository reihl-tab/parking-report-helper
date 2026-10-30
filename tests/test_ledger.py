"""Functional durable ledger tests use only temporary local Git repositories."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    from parking_report.api106 import ReviewNeeded, submit
    from parking_report.ledger import GitLedger, LedgerError
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
        with self.assertRaises(LedgerError), GitLedger(b).locked():
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
        with self.assertRaises(LedgerError), GitLedger(self.a).locked() as ledger:
            ledger.reserve("one", "a" * 64, "b" * 64)
        with self.assertRaises(LedgerError), GitLedger(self.a).locked():
            pass

    def followup_manifest(self, parent="original"):
        return {
            "parent_id": parent,
            "parent_digest": "a" * 64,
            "parent_case_number": "2601-1234567",
            "parent_closed_at": "2026-01-01T12:00:00+02:00",
            "approval_reference": "approval-synthetic-1",
        }

    def test_approved_followup_preserves_photo_index_and_receipt(self):
        with GitLedger(self.a).locked() as ledger:
            ledger.reserve("original", "a" * 64, "b" * 64)
            ledger.checkpoint("original", {"version": 1, "status": "complete"})
            index = dict(ledger.data["photos"])
            manifest = self.followup_manifest()
            ledger.reserve("followup", "c" * 64, "b" * 64, followup=manifest)
            self.assertEqual(ledger.data["photos"], index)
            with self.assertRaises(LedgerError):
                ledger.reserve("ordinary-duplicate", "d" * 64, "b" * 64)
            with self.assertRaises(LedgerError):
                ledger.reserve("sibling", "d" * 64, "b" * 64, followup=manifest)
            ledger.checkpoint("followup", {"version": 1, "status": "complete"})
            self.assertEqual(
                ledger.reserve("followup", "c" * 64, "b" * 64, followup=manifest)[
                    "state"
                ]["status"],
                "complete",
            )
            changed = dict(manifest, approval_reference="other-approval")
            with self.assertRaises(LedgerError):
                ledger.reserve("followup", "c" * 64, "b" * 64, followup=changed)

    def test_followup_requires_matching_complete_parent(self):
        with GitLedger(self.a).locked() as ledger:
            ledger.reserve("original", "a" * 64, "b" * 64)
            manifest = self.followup_manifest()
            for status in ("prepared", "creating", "created", "attaching"):
                ledger.checkpoint("original", {"version": 1, "status": status})
                with self.assertRaises(LedgerError):
                    ledger.reserve("followup", "c" * 64, "b" * 64, followup=manifest)
            ledger.checkpoint("original", {"version": 1, "status": "complete"})
            for bad in (
                dict(manifest, parent_id="missing"),
                dict(manifest, parent_digest="e" * 64),
            ):
                with self.assertRaises(LedgerError):
                    ledger.reserve("followup", "c" * 64, "b" * 64, followup=bad)
            with self.assertRaises(LedgerError):
                ledger.reserve("followup", "c" * 64, "f" * 64, followup=manifest)

    def test_followup_round_two_and_receipt_reference(self):
        with GitLedger(self.a).locked() as ledger:
            ledger.reserve("original", "a" * 64, "b" * 64)
            ledger.checkpoint(
                "original",
                {
                    "version": 1,
                    "status": "complete",
                    "case": {"CaseNumberInCRM": "2601-1234567"},
                },
            )
            with self.assertRaises(LedgerError):
                ledger.reserve(
                    "child",
                    "c" * 64,
                    "b" * 64,
                    followup=dict(
                        self.followup_manifest(), parent_case_number="2601-9"
                    ),
                )
            ledger.reserve(
                "child", "c" * 64, "b" * 64, followup=self.followup_manifest()
            )
            ledger.checkpoint("child", {"version": 1, "status": "complete"})
            with self.assertRaises(LedgerError):
                ledger.reserve(
                    "round-two", "d" * 64, "b" * 64, followup=self.followup_manifest()
                )
            second = dict(
                self.followup_manifest("child"),
                parent_digest="c" * 64,
                parent_case_number="2601-1234568",
                approval_reference="approval-2",
            )
            ledger.reserve("round-two", "d" * 64, "b" * 64, followup=second)

    def test_followup_preview_leaves_real_git_ledger_unchanged(self):
        from test_api_cli_main import make_folder, run

        from parking_report.api106 import read_picture

        folder = self.root / "preview"
        folder.mkdir()
        folder, addresses = make_folder(folder)
        photo_hash, _, _ = read_picture(folder / "photo.jpg")
        with GitLedger(self.a).locked() as ledger:
            ledger.reserve("original", "a" * 64, photo_hash)
            ledger.checkpoint("original", {"version": 1, "status": "complete"})
        before_bytes = (self.a / "ledger.json").read_bytes()
        before_remote = self.git(self.a, "ls-remote", "origin", "refs/heads/106-ledger")
        record = json.loads((folder / "report.json").read_text())
        record["followup"] = self.followup_manifest()
        (folder / "report.json").write_text(json.dumps(record))
        code, _ = run(
            [str(folder), "--addresses", str(addresses), "--ledger-dir", str(self.a)]
        )
        self.assertEqual(code, 0)
        self.assertEqual((self.a / "ledger.json").read_bytes(), before_bytes)
        self.assertEqual(
            self.git(self.a, "ls-remote", "origin", "refs/heads/106-ledger"),
            before_remote,
        )
