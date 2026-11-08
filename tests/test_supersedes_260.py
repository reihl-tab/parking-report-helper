"""Sanctioned corrected re-filing of an open case on the same photo (#260).

Synthetic fixtures only: temporary local Git ledgers, fake digests and the
shared synthetic intake folder. No municipal request is made.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import test_ledger
from test_api_cli_main import ENV, make_folder, run

from parking_report import api106, api_cli
from parking_report.api106 import read_picture
from parking_report.ledger import GitLedger, LedgerError
from parking_report.supersedes import SUPERSEDABLE_STATES, validate_supersedes

OPEN_STATES = ("prepared", "creating", "created", "attaching", "complete")
CORRECTED_TEXT = "גרפיטי על קיר ברחוב לדוגמה 1. אבקש לטפל במפגע. מחליף פנייה קודמת."


def manifest(parent="original", digest="a" * 64):
    return {
        "parent_id": parent,
        "parent_digest": digest,
        "approval_reference": "approval-synthetic-260",
    }


def key(report_id):
    return hashlib.sha256(report_id.encode()).hexdigest()


class SupersedesValidationTests(unittest.TestCase):
    def test_supersedes_exact_manifest_validates(self):
        self.assertEqual(validate_supersedes(manifest()), manifest())
        self.assertEqual(validate_supersedes(manifest(), "corrected"), manifest())

    def test_supersedes_rejects_missing_unknown_and_malformed_fields(self):
        bad_values = [None, {}, [], dict(manifest(), extra="bad")]
        for field in manifest():
            missing = manifest()
            missing.pop(field)
            bad_values.append(missing)
        bad_values += [
            dict(manifest(), parent_case_number="2601-1234567"),
            dict(manifest(), parent_closed_at="2026-01-01T12:00:00+02:00"),
            dict(manifest(), parent_digest="bad"),
            dict(manifest(), parent_digest="A" * 64),
            dict(manifest(), parent_id="../bad"),
            dict(manifest(), parent_id=""),
            dict(manifest(), approval_reference=" "),
            dict(manifest(), approval_reference=" padded"),
            dict(manifest(), approval_reference=7),
        ]
        for bad in bad_values:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_supersedes(bad)

    def test_supersedes_rejects_self_reference(self):
        with self.assertRaises(ValueError):
            validate_supersedes(manifest("same-id"), "same-id")

    def test_supersedes_open_states_are_the_ledger_states(self):
        """The allow-list is exactly the saved states api106.submit accepts."""
        source = Path(api106.__file__).read_text(encoding="utf-8")
        known = [
            {element.value for element in node.comparators[0].elts}
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Compare)
            and isinstance(node.ops[0], ast.NotIn)
            and isinstance(node.comparators[0], ast.Set)
            and any(
                isinstance(e, ast.Constant) and e.value == "prepared"
                for e in node.comparators[0].elts
            )
        ]
        self.assertEqual(known, [set(OPEN_STATES)])
        self.assertEqual(SUPERSEDABLE_STATES, frozenset(OPEN_STATES))


class SupersedesLedgerTests(unittest.TestCase):
    setUp = test_ledger.LedgerTests.setUp
    git = test_ledger.LedgerTests.git
    clone = test_ledger.LedgerTests.clone

    def parent(self, ledger, status="prepared", report_id="original", photo="b"):
        ledger.reserve(report_id, "a" * 64, photo * 64)
        if status != "prepared":
            ledger.checkpoint(report_id, {"version": 1, "status": status})

    def test_supersedes_reserve_permits_parent_photo_in_every_open_state(self):
        with GitLedger(self.a).locked() as ledger:
            for index, status in enumerate(OPEN_STATES):
                photo = "0123456789"[index]
                parent_id = "parent-" + status
                with self.subTest(status=status):
                    self.parent(ledger, status, parent_id, photo)
                    entry = ledger.reserve(
                        "child-" + status,
                        "c" * 64,
                        photo * 64,
                        supersedes=manifest(parent_id),
                    )
                    self.assertEqual(entry["supersedes"], manifest(parent_id))
                    self.assertEqual(entry["state"]["status"], "prepared")

    def test_supersedes_absent_still_blocks_parent_photo(self):
        with GitLedger(self.a).locked() as ledger:
            self.parent(ledger)
            before = copy.deepcopy(ledger.data)
            with self.assertRaises(LedgerError):
                ledger.reserve("corrected", "c" * 64, "b" * 64)
            self.assertEqual(ledger.data, before)

    def test_supersedes_exact_duplicate_digest_blocked(self):
        with GitLedger(self.a).locked() as ledger:
            self.parent(ledger)
            before = copy.deepcopy(ledger.data)
            with self.assertRaises(LedgerError):
                ledger.reserve("corrected", "a" * 64, "b" * 64, supersedes=manifest())
            self.assertEqual(ledger.data, before)

    def test_supersedes_ledger_rejections_leave_ledger_unchanged(self):
        missing_field = manifest()
        missing_field.pop("approval_reference")
        cases = {
            "missing field": ("corrected", "b", missing_field),
            "unknown field": ("corrected", "b", dict(manifest(), extra="x")),
            "followup field": (
                "corrected",
                "b",
                dict(manifest(), parent_case_number="2601-1234567"),
            ),
            "self reference": ("original", "b", manifest()),
            "unknown parent": ("corrected", "b", manifest("missing-parent")),
            "digest mismatch": ("corrected", "b", manifest(digest="e" * 64)),
            "other photo": ("corrected", "f", manifest()),
        }
        with GitLedger(self.a).locked() as ledger:
            self.parent(ledger)
            # Another ordinary intake owns photo "f": a parent's link never
            # extends to someone else's original.
            ledger.reserve("other-intake", "9" * 64, "f" * 64)
            before = copy.deepcopy(ledger.data)
            for name, (report_id, photo, bad) in cases.items():
                with self.subTest(name), self.assertRaises(LedgerError):
                    ledger.reserve(report_id, "c" * 64, photo * 64, supersedes=bad)
                self.assertEqual(ledger.data, before)

    def test_supersedes_requires_the_photo_index(self):
        with GitLedger(self.a).locked() as ledger:
            self.parent(ledger)
            for index in ({}, {"b" * 64: key("vanished")}):
                ledger.data["photos"] = index
                with self.subTest(index=index), self.assertRaises(LedgerError):
                    ledger.reserve(
                        "corrected", "c" * 64, "b" * 64, supersedes=manifest()
                    )
                self.assertNotIn(
                    "superseded_by", ledger.data["reports"][key("original")]
                )

    def test_supersedes_rejects_parent_state_outside_allow_list(self):
        with GitLedger(self.a).locked() as ledger:
            self.parent(ledger)
            entry = ledger.data["reports"][key("original")]
            for state in (
                {"version": 1, "status": "closed"},
                {"version": 1, "status": "already_submitted"},
                {"version": 1, "status": ""},
                {"version": 1},
                "complete",
                None,
            ):
                entry["state"] = state
                with self.subTest(state=state), self.assertRaises(LedgerError):
                    ledger.reserve(
                        "corrected", "c" * 64, "b" * 64, supersedes=manifest()
                    )
                self.assertNotIn("superseded_by", entry)
                self.assertNotIn(key("corrected"), ledger.data["reports"])
            entry.pop("state")
            with self.assertRaises(LedgerError):
                ledger.reserve("corrected", "c" * 64, "b" * 64, supersedes=manifest())

    def test_supersedes_rejects_non_ordinary_or_already_superseded_parent(self):
        with GitLedger(self.a).locked() as ledger:
            self.parent(ledger, "complete")
            followup = dict(
                test_ledger.LedgerTests.followup_manifest(None),
            )
            ledger.reserve("followup-child", "d" * 64, "b" * 64, followup=followup)
            with self.assertRaises(LedgerError):
                ledger.reserve(
                    "corrected",
                    "c" * 64,
                    "b" * 64,
                    supersedes=manifest("followup-child", "d" * 64),
                )
            ledger.reserve("corrected", "c" * 64, "b" * 64, supersedes=manifest())
            with self.assertRaises(LedgerError):
                ledger.reserve(
                    "second-correction", "e" * 64, "b" * 64, supersedes=manifest()
                )
            self.assertEqual(
                ledger.data["reports"][key("original")]["superseded_by"], "corrected"
            )

    def test_supersedes_cannot_combine_other_relations(self):
        with GitLedger(self.a).locked() as ledger:
            self.parent(ledger, "complete")
            followup = test_ledger.LedgerTests.followup_manifest(None)
            with self.assertRaises(LedgerError):
                ledger.reserve(
                    "corrected",
                    "c" * 64,
                    "b" * 64,
                    followup=followup,
                    supersedes=manifest(),
                )

    def test_supersedes_audit_links_written_and_parent_fields_unchanged(self):
        with GitLedger(self.a).locked() as ledger:
            self.parent(ledger, "created")
            ledger.data["reports"][key("original")]["state"]["case"] = {
                "CaseNumberInCRM": "2601-1234567"
            }
            ledger.persist()
            parent_before = copy.deepcopy(ledger.data["reports"][key("original")])
            photos_before = dict(ledger.data["photos"])
            child = ledger.reserve(
                "corrected", "c" * 64, "b" * 64, supersedes=manifest()
            )
        clone = self.root / "verify"
        self.clone(clone)
        self.git(clone, "checkout", "106-ledger")
        data = json.loads((clone / "ledger.json").read_text())
        parent = data["reports"][key("original")]
        self.assertEqual(parent.pop("superseded_by"), "corrected")
        self.assertEqual(parent, parent_before)
        self.assertEqual(data["reports"][key("corrected")], child)
        self.assertEqual(child["supersedes"], manifest())
        self.assertEqual(child["photo_sha256"], "b" * 64)
        self.assertEqual(data["photos"], photos_before)
        self.assertEqual(data["photos"]["b" * 64], key("original"))

    def test_supersedes_retry_is_idempotent_and_changed_manifest_refused(self):
        with GitLedger(self.a).locked() as ledger:
            self.parent(ledger)
            first = ledger.reserve(
                "corrected", "c" * 64, "b" * 64, supersedes=manifest()
            )
            self.assertEqual(
                ledger.reserve("corrected", "c" * 64, "b" * 64, supersedes=manifest()),
                first,
            )
            for changed in (
                None,
                dict(manifest(), approval_reference="approval-other"),
            ):
                with self.subTest(changed=changed), self.assertRaises(LedgerError):
                    ledger.reserve("corrected", "c" * 64, "b" * 64, supersedes=changed)
            # The superseded parent's own retry still returns its entry.
            parent = ledger.reserve("original", "a" * 64, "b" * 64)
            self.assertEqual(parent["superseded_by"], "corrected")
            with self.assertRaises(LedgerError):
                ledger.reserve("ordinary-duplicate", "d" * 64, "b" * 64)


def _reporter():
    return {
        key_: ENV["PARKING_REPORTER_" + key_.upper()]
        for key_ in ("first_name", "last_name", "phone", "id_number", "email")
    }


class _CliFlow:
    """Shared CLI helpers; mixed into the mocked and the real-ledger classes."""

    def folder(self, tmp, supersedes=None, corrected=True):
        folder, addresses = make_folder(tmp)
        ordinary = api_cli.prepare(folder, addresses, _reporter())[3]
        record = json.loads((folder / "report.json").read_text())
        if corrected:
            record["approved_description"] = CORRECTED_TEXT
        if supersedes is not None:
            record["supersedes"] = supersedes
        (folder / "report.json").write_text(json.dumps(record))
        return folder, addresses, ordinary

    def preview(self, folder, addresses, *extra):
        code, output = run([str(folder), "--addresses", str(addresses), *extra])
        return code, json.loads(output)

    def live(self, folder, addresses, ledger_dir, normal, supersedes, enabled="true"):
        return run(
            [
                str(folder),
                "--addresses",
                str(addresses),
                "--ledger-dir",
                str(ledger_dir),
                "--submit",
                "--approved-sha256",
                normal,
                "--approved-supersedes-sha256",
                supersedes,
            ],
            dict(ENV, PARKING_106_ENABLE_LIVE=enabled),
        )


class SupersedesCliTests(_CliFlow, unittest.TestCase):
    def test_supersedes_manifest_bound_into_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, addresses, _ = self.folder(tmp)
            _, plain = self.preview(folder, addresses)
            digests = []
            for evidence in ("approval-1", "approval-2"):
                record = json.loads((folder / "report.json").read_text())
                record["supersedes"] = dict(manifest(), approval_reference=evidence)
                (folder / "report.json").write_text(json.dumps(record))
                code, output = self.preview(folder, addresses)
                self.assertEqual(code, 0)
                self.assertEqual(output["status"], "dry_run")
                digests.append(output["sha256"])
            self.assertNotEqual(*digests)
            self.assertNotIn(plain["sha256"], digests)

    def test_supersedes_cli_refuses_exact_duplicate_self_and_combination(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, addresses, ordinary = self.folder(tmp, corrected=False)
            record = json.loads((folder / "report.json").read_text())
            for name, value in (
                ("exact duplicate", {"supersedes": manifest(digest=ordinary)}),
                ("self reference", {"supersedes": manifest(folder.name)}),
                ("unknown field", {"supersedes": dict(manifest(), extra="x")}),
                (
                    "combined followup",
                    {
                        "supersedes": manifest(),
                        "followup": test_ledger.LedgerTests.followup_manifest(None),
                    },
                ),
            ):
                (folder / "report.json").write_text(json.dumps(dict(record, **value)))
                with self.subTest(name):
                    code, output = self.preview(folder, addresses)
                    self.assertEqual(code, 4)
                    self.assertEqual(output["status"], "blocked")

    def test_supersedes_cli_digest_mismatch_refused_before_reservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, addresses, _ = self.folder(tmp, manifest())
            _, output = self.preview(folder, addresses)
            digest = output["sha256"]
            for normal, approved, enabled in (
                (digest, "wrong", "true"),
                (digest, "", "true"),
                (digest, "f" * 64, "true"),
                ("wrong", digest, "true"),
                (digest, digest, "false"),
            ):
                ledger = MagicMock()
                ledger.locked.return_value.__enter__.return_value = ledger
                with (
                    self.subTest(normal=normal, approved=approved, enabled=enabled),
                    patch("parking_report.ledger.GitLedger", return_value=ledger),
                    patch.object(api_cli, "execute", return_value=0) as execute,
                ):
                    code, out = self.live(
                        folder, addresses, tmp, normal, approved, enabled
                    )
                    self.assertEqual(code, 4)
                    self.assertEqual(json.loads(out)["status"], "blocked")
                    ledger.reserve.assert_not_called()
                    execute.assert_not_called()

    def test_supersedes_approval_without_manifest_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, addresses, _ = self.folder(tmp)
            _, output = self.preview(folder, addresses)
            digest = output["sha256"]
            code, _ = self.preview(
                folder, addresses, "--approved-supersedes-sha256", digest
            )
            self.assertEqual(code, 4)

    def test_supersedes_explicit_gate_passes_manifest_to_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, addresses, _ = self.folder(tmp, manifest())
            _, output = self.preview(folder, addresses)
            digest = output["sha256"]
            ledger = MagicMock()
            ledger.locked.return_value.__enter__.return_value = ledger
            with (
                patch("parking_report.ledger.GitLedger", return_value=ledger),
                patch.object(api_cli, "execute", return_value=0) as execute,
            ):
                code, _ = self.live(folder, addresses, tmp, digest, digest)
            self.assertEqual(code, 0)
            self.assertEqual(
                ledger.reserve.call_args.kwargs, {"supersedes": manifest()}
            )
            self.assertEqual(ledger.reserve.call_args.args[0], folder.name)
            execute.assert_called_once()


class SupersedesCliLedgerTests(_CliFlow, unittest.TestCase):
    """The same CLI flow against a real temporary Git ledger."""

    setUp = test_ledger.LedgerTests.setUp
    git = test_ledger.LedgerTests.git
    clone = test_ledger.LedgerTests.clone

    def intake(self, parent_status="prepared"):
        folder = self.root / "corrected-intake"
        folder.mkdir()
        folder, addresses, ordinary = self.folder(folder)
        photo_hash, _, _ = read_picture(folder / "photo.jpg")
        with GitLedger(self.a).locked() as ledger:
            ledger.reserve("original", ordinary, photo_hash)
            if parent_status != "prepared":
                ledger.checkpoint("original", {"version": 1, "status": parent_status})
        record = json.loads((folder / "report.json").read_text())
        record["supersedes"] = manifest(digest=ordinary)
        (folder / "report.json").write_text(json.dumps(record))
        return folder, addresses, ordinary, photo_hash

    def snapshot(self):
        return (
            (self.a / "ledger.json").read_bytes(),
            self.git(self.a, "ls-remote", "origin", "refs/heads/106-ledger"),
        )

    def test_supersedes_dry_run_does_not_reserve(self):
        folder, addresses, _, _ = self.intake()
        before = self.snapshot()
        code, output = self.preview(folder, addresses, "--ledger-dir", str(self.a))
        self.assertEqual(code, 0)
        self.assertEqual(output["status"], "dry_run")
        self.assertEqual(self.snapshot(), before)
        data = json.loads((self.a / "ledger.json").read_text())
        self.assertNotIn(key(folder.name), data["reports"])
        self.assertNotIn("superseded_by", data["reports"][key("original")])

    def test_supersedes_live_gate_reserves_with_both_links(self):
        folder, addresses, ordinary, photo_hash = self.intake("complete")
        _, output = self.preview(folder, addresses)
        digest = output["sha256"]
        with patch.object(api_cli, "execute", return_value=0) as execute:
            code, _ = self.live(folder, addresses, self.a, digest, digest)
        self.assertEqual(code, 0)
        entry = execute.call_args.args[6]
        self.assertEqual(entry["supersedes"], manifest(digest=ordinary))
        data = json.loads((self.a / "ledger.json").read_text())
        child = data["reports"][key(folder.name)]
        parent = data["reports"][key("original")]
        self.assertEqual(child["digest"], digest)
        self.assertEqual(child["photo_sha256"], photo_hash)
        self.assertEqual(parent["superseded_by"], folder.name)
        self.assertEqual(
            (parent["digest"], parent["state"]),
            (ordinary, {"version": 1, "status": "complete"}),
        )
        self.assertEqual(data["photos"], {photo_hash: key("original")})


if __name__ == "__main__":
    unittest.main()
