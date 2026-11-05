"""Controlled shared-photo extension after a completed intake (#293).

Synthetic fixtures only: fake plates, generated images, fake case receipts,
synthetic guard seeds and temporary local Git ledgers. No network, no real
seeds, plates or evidence.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

from PIL import Image
from synthetic_contact import SYNTHETIC_ID, SYNTHETIC_PHONE

from parking_report import api_cli
from parking_report.ledger import GitLedger, LedgerError
from parking_report.shared_photo_extension import (
    DIGEST_FIELD,
    PAYLOAD_NAME,
    corroborate_parent,
    extension_manifest,
    extension_relation,
    validate_extension,
)
from parking_report.vehicle_guard import GuardKey

ENV = {
    "PARKING_REPORTER_FIRST_NAME": "דנה",
    "PARKING_REPORTER_LAST_NAME": "לוי",
    "PARKING_REPORTER_PHONE": SYNTHETIC_PHONE,
    "PARKING_REPORTER_ID_NUMBER": SYNTHETIC_ID,
    "PARKING_REPORTER_EMAIL": "dana@example.com",
}
SEED = bytes(range(32))  # Synthetic, deterministic test secret only.
OTHER_SEED = bytes(range(1, 33))
KEY_ID = "test-v1"
GUARD_ENV = dict(ENV, PARKING_GUARD_SEED=SEED.hex(), PARKING_GUARD_KEY_ID=KEY_ID)
LIVE = dict(GUARD_ENV, PARKING_106_ENABLE_LIVE="true")
# Fake plates: the records use the dashed form, manifests the normalized digits.
PLATE_A, PLATE_B, PLATE_C = "1000001", "20000002", "3000003"
DASHED = {PLATE_A: "10-000-01", PLATE_B: "200-00-002", PLATE_C: "30-000-03"}
REFERENCE = "approval-synthetic-293"

BLOCKED = {
    "status": "blocked",
    "note": "Check private input and ledger; no automatic retry.",
}


class Out(io.StringIO):
    def reconfigure(self, encoding=None):
        pass


class FakeMunicipality:
    """Counts creations and captures payloads; listed plates fail (uncertain)."""

    created: ClassVar[list[str]] = []
    payloads: ClassVar[list[dict]] = []
    failing: ClassVar[set[str]] = set()

    def __init__(self, credentials):
        pass

    def login(self):
        return "fake-token"

    def contact(self, token, reporter):
        return {}

    def create(self, token, contact, report):
        description = report["CaseDescription"]
        if any(DASHED[plate] in description for plate in self.failing):
            raise OSError("synthetic timeout")
        self.created.append(description)
        self.payloads.append(dict(report))
        number = len(self.created)
        return {
            "CaseIdInCRM": f"fake-case-{number}",
            "WebCaseIdInCRM": f"fake-web-{number}",
            "CaseNumberInCRM": f"2601-{number:07d}",
        }

    def attach(self, token, case, jpeg, name):
        pass


def git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout


class ExtensionBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.intakes = self.root / "intakes"
        self.intakes.mkdir()
        self.addresses = self.root / "addresses.json"
        self.addresses.write_text(
            json.dumps(
                {
                    "version": 1,
                    "streets": [{"StreetCode": "fake", "StreetName": "רחוב לדוגמה"}],
                    "addresses": [[31.77, 35.21, "1", "רחוב לדוגמה"]],
                }
            ),
            encoding="utf-8",
        )
        self.ledger_dir = self.make_ledger()
        self.outputs: list[str] = []
        FakeMunicipality.created = []
        FakeMunicipality.payloads = []
        FakeMunicipality.failing = set()
        self.guard = GuardKey(KEY_ID, SEED)

    def make_ledger(self):
        remote = self.root / f"remote-{len(list(self.root.glob('remote-*')))}.git"
        subprocess.run(
            ["git", "init", "--bare", str(remote)], capture_output=True, check=True
        )
        clone = Path(str(remote).removesuffix(".git"))
        subprocess.run(["git", "clone", str(remote), str(clone)], capture_output=True)
        git(clone, "config", "user.name", "Test")
        git(clone, "config", "user.email", "test@example.invalid")
        git(clone, "checkout", "--orphan", "106-ledger")
        (clone / "ledger.json").write_text(
            json.dumps({"version": 1, "reports": {}, "photos": {}})
        )
        git(clone, "add", ".")
        git(clone, "commit", "-m", "Initialize")
        git(clone, "push", "origin", "HEAD:106-ledger")
        return clone

    def provision(self, ledger_dir=None, key=None):
        with GitLedger(ledger_dir or self.ledger_dir).locked() as ledger:
            ledger.provision_guard_key(key or self.guard)

    def ledger_text(self):
        return (self.ledger_dir / "ledger.json").read_text(encoding="utf-8")

    def ledger_data(self):
        return json.loads(self.ledger_text())

    def edit_ledger(self, change):
        """Rewrite ledger.json through a reviewed commit, as an operator would."""
        path = self.ledger_dir / "ledger.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        change(data)
        path.write_text(json.dumps(data), encoding="utf-8")
        git(self.ledger_dir, "add", ".")
        git(self.ledger_dir, "commit", "-m", "Reviewed ledger edit")
        git(self.ledger_dir, "push", "origin", "HEAD:106-ledger")

    def car(self, intake_id, plate, color="white", photo_from=None, subject=None):
        folder = self.intakes / intake_id
        folder.mkdir()
        if photo_from is None:
            Image.new("RGB", (10, 10), color).save(folder / "photo.jpg")
        else:
            (folder / "photo.jpg").write_bytes((photo_from / "photo.jpg").read_bytes())
        record = {
            "schema_version": 1,
            "status": "ready",
            "photo": "photo.jpg",
            "captured_at": "2026-01-01 12:00",
            "plate": DASHED.get(plate, plate),
            "address": {"street": "רחוב לדוגמה", "house_number": "1"},
            "violation": {
                "category": "sidewalk_parking",
                "description": "חונה על המדרכה",
            },
        }
        if subject is not None:
            record.update(subject)
        self.write(folder, record)
        return folder

    @staticmethod
    def write(folder, record):
        (folder / "report.json").write_text(json.dumps(record), encoding="utf-8")

    @staticmethod
    def record(folder):
        return json.loads((folder / "report.json").read_text(encoding="utf-8"))

    @staticmethod
    def photo_hash(folder):
        return hashlib.sha256((folder / "photo.jpg").read_bytes()).hexdigest()

    def key(self, intake_id):
        return hashlib.sha256(intake_id.encode()).hexdigest()

    def run_cli(self, argv, env=GUARD_ENV):
        out, err = Out(), Out()
        with (
            patch.object(sys, "stdout", out),
            patch.object(sys, "stderr", err),
            patch.dict("os.environ", env, clear=False),
        ):
            code = api_cli.main([str(a) for a in argv])
        self.outputs += [out.getvalue(), err.getvalue()]
        return code, out.getvalue()

    def preview(self, folder, *extra, env=GUARD_ENV):
        code, out = self.run_cli(
            [folder, "--addresses", self.addresses, *extra], env=env
        )
        self.assertEqual(code, 0, out)
        return json.loads(out)["sha256"]

    def submit(self, folder, extra=(), env=LIVE, digest=None):
        digest = digest or self.preview(folder)
        argv = [
            folder,
            "--addresses",
            self.addresses,
            "--ledger-dir",
            self.ledger_dir,
            "--state-dir",
            self.root / "state",
            "--submit",
            "--approved-sha256",
            digest,
            *extra,
        ]
        with (
            patch("parking_report.api106.Municipality", FakeMunicipality),
            patch.object(api_cli, "post_submit_duplicate_check", return_value=[]),
        ):
            code, out = self.run_cli(argv, env)
        return code, json.loads(out), digest

    def submit_ordinary(self, folder, digest=None):
        return self.submit(folder, digest=digest)

    def submit_child(self, folder, approval=None, env=LIVE, digest=None):
        extra = []
        if approval is not False:
            if approval is None:
                approval = digest or self.preview(folder)
            extra = ["--approved-extension-sha256", approval]
        return self.submit(folder, extra=extra, env=env, digest=digest)

    def complete_parent(self, plate=PLATE_A, intake_id="parent-a", **kwargs):
        """One completed ordinary intake plus the recorded digest artifact."""
        folder = self.car(intake_id, plate, **kwargs)
        code, out, digest = self.submit_ordinary(folder)
        self.assertEqual((code, out["status"]), (0, "submitted"))
        record = self.record(folder)
        record[DIGEST_FIELD] = digest
        self.write(folder, record)
        return folder, digest

    def manifest(self, parent_digest, photo, **overrides):
        value = {
            "parent_id": "parent-a",
            "parent_digest": parent_digest,
            "photo_sha256": photo,
            "parent_plate": PLATE_A,
            "plate": PLATE_B,
            "authority_reference": REFERENCE,
        }
        value.update(overrides)
        return value

    def child(self, parent, parent_digest, intake_id="child-b", **overrides):
        plate = overrides.pop("plate", PLATE_B)
        overrides.setdefault("plate", plate)
        folder = self.car(intake_id, plate, photo_from=parent)
        record = self.record(folder)
        record["shared_photo_extension"] = self.manifest(
            parent_digest, self.photo_hash(parent), **overrides
        )
        self.write(folder, record)
        return folder

    def family(self):
        """Provisioned ledger, completed parent, valid child; nothing reserved."""
        self.provision()
        parent, digest = self.complete_parent()
        return parent, digest, self.child(parent, digest)

    def assert_blocked_quietly(self, folder, **kwargs):
        before = git(self.ledger_dir, "rev-parse", "HEAD")
        code, out, _ = self.submit_child(folder, **kwargs)
        self.assertEqual((code, out), (4, BLOCKED))
        self.assertEqual(git(self.ledger_dir, "rev-parse", "HEAD"), before)
        return out


class ManifestValidationTests(ExtensionBase):
    def test_exact_manifest_and_every_rejection(self):
        good = self.manifest("a" * 64, "b" * 64)
        self.assertEqual(validate_extension(good), good)
        bad_cases = {
            "not an object": None,
            "missing field": {k: v for k, v in good.items() if k != "plate"},
            "extra field": dict(good, extra="x"),
            "unsafe parent ID": dict(good, parent_id="../a"),
            "bad parent digest": dict(good, parent_digest="A" * 64),
            "bad photo hash": dict(good, photo_sha256="b" * 63),
            "same car twice": dict(good, parent_plate=PLATE_B),
            "plate not normalized": dict(good, plate=DASHED[PLATE_B]),
            "parent plate not normalized": dict(good, parent_plate=DASHED[PLATE_A]),
            "non-standard plate": dict(good, plate="123456"),
            "letters in plate": dict(good, plate="12א34567"),
            "blank authority": dict(good, authority_reference=" "),
            "authority not text": dict(good, authority_reference=1),
        }
        for name, bad in bad_cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                validate_extension(bad)

    def test_relation_is_plate_free(self):
        manifest = self.manifest("a" * 64, "b" * 64)
        relation = extension_relation(manifest)
        self.assertEqual(
            set(relation),
            {"parent", "parent_digest", "photo_sha256", "authority_reference"},
        )
        self.assertEqual(relation["parent"], self.key("parent-a"))
        for plate in (PLATE_A, PLATE_B):
            self.assertNotIn(plate, json.dumps(relation))

    def test_combination_with_followup_or_shared_photo_is_rejected(self):
        record = {
            "plate": PLATE_B,
            "shared_photo_extension": self.manifest("a" * 64, "b" * 64),
        }
        for extra in ("followup", "shared_photo"):
            with self.subTest(extra):
                with self.assertRaises(ValueError):
                    extension_manifest(dict(record, **{extra: {}}), "child-b", "b" * 64)

    def test_manifest_binds_own_photo_plate_and_vehicle_subject(self):
        record = {"plate": DASHED[PLATE_B]}
        manifest = self.manifest("a" * 64, "b" * 64)
        record["shared_photo_extension"] = manifest
        self.assertEqual(extension_manifest(record, "child-b", "b" * 64), manifest)
        with self.assertRaises(ValueError):
            extension_manifest(record, "child-b", "c" * 64)
        mismatch = dict(record, plate=DASHED[PLATE_C])
        with self.assertRaises(ValueError):
            extension_manifest(mismatch, "child-b", "b" * 64)


class HappyPathTests(ExtensionBase):
    def test_one_distinct_new_car_from_the_same_original(self):
        parent, parent_digest, child = self.family()
        before_parent = json.loads(json.dumps(self.ledger_data()["reports"]))
        code, out, child_digest = self.submit_child(child)
        self.assertEqual((code, out["status"]), (0, "submitted"))
        self.assertNotEqual(child_digest, parent_digest)
        self.assertEqual(len(FakeMunicipality.created), 2)
        data = self.ledger_data()
        photo = self.photo_hash(parent)
        # The child entry carries the plate-free relation and its own receipt.
        entry = data["reports"][self.key("child-b")]
        self.assertEqual(
            entry["extension"],
            {
                "parent": self.key("parent-a"),
                "parent_digest": parent_digest,
                "photo_sha256": photo,
                "authority_reference": REFERENCE,
            },
        )
        self.assertEqual(entry["digest"], child_digest)
        self.assertEqual(entry["state"]["status"], "complete")
        # The parent entry and the original photo index are byte-unchanged.
        self.assertEqual(
            data["reports"][self.key("parent-a")], before_parent[self.key("parent-a")]
        )
        self.assertEqual(data["photos"], {photo: self.key("parent-a")})
        self.assertEqual(data["version"], 1)
        self.assertNotIn("shared_photos", data)
        # The private index holds the derived guard key and the bounded slot.
        index = data["photo_vehicle"]
        self.assertEqual(index["key"], self.guard.metadata())
        self.assertEqual(
            index["guards"],
            {self.guard.identity(photo, PLATE_B): self.key("child-b")},
        )
        self.assertEqual(index["extensions"], {photo: self.key("child-b")})

    def test_municipal_payload_artifact_also_corroborates(self):
        self.provision()
        parent, parent_digest = self.complete_parent()
        record = self.record(parent)
        del record[DIGEST_FIELD]
        self.write(parent, record)
        (parent / PAYLOAD_NAME).write_text(
            json.dumps(FakeMunicipality.payloads[0]), encoding="utf-8"
        )
        child = self.child(parent, parent_digest)
        code, out, _ = self.submit_child(child)
        self.assertEqual((code, out["status"]), (0, "submitted"))

    def test_conflicting_artifacts_fail_closed(self):
        self.provision()
        parent, parent_digest = self.complete_parent()
        (parent / PAYLOAD_NAME).write_text(
            json.dumps(dict(FakeMunicipality.payloads[0], Language=2)),
            encoding="utf-8",
        )
        child = self.child(parent, parent_digest)
        self.assert_blocked_quietly(child)
        self.assertEqual(len(FakeMunicipality.created), 1)

    def test_extension_preview_reserves_nothing(self):
        _, _, child = self.family()
        before = git(self.ledger_dir, "rev-parse", "HEAD")
        self.preview(child, "--ledger-dir", self.ledger_dir)
        self.assertEqual(git(self.ledger_dir, "rev-parse", "HEAD"), before)
        self.assertNotIn(self.key("child-b"), self.ledger_data()["reports"])


class DuplicateIdentityTests(ExtensionBase):
    def test_same_plate_spelling_cannot_extend(self):
        parent, parent_digest, child = self.family()
        record = self.record(child)
        for claimed in (PLATE_A, DASHED[PLATE_A]):
            with self.subTest(claimed):
                record["shared_photo_extension"]["plate"] = claimed
                self.write(child, record)
                code, _ = self.run_cli([child, "--addresses", self.addresses])
                self.assertEqual(code, 4)
        self.assertEqual(len(FakeMunicipality.created), 1)
        self.assertNotIn(self.key("child-b"), self.ledger_data()["reports"])

    def test_wrong_photo_or_digest_and_missing_authority_fail_first(self):
        parent, parent_digest, _ = self.family()
        cases = {
            "wrong photo": dict(photo_sha256="0" * 64),
            "wrong digest": dict(parent_digest="0" * 64),
            "other parent": dict(parent_id="parent-z"),
        }
        for index, (name, override) in enumerate(cases.items()):
            with self.subTest(name):
                child = self.child(parent, parent_digest, f"child-w{index}")
                record = self.record(child)
                record["shared_photo_extension"].update(override)
                self.write(child, record)
                if name == "wrong photo":
                    # The manifest binds the real original: preview refuses.
                    code, _ = self.run_cli([child, "--addresses", self.addresses])
                    self.assertEqual(code, 4)
                else:
                    self.assert_blocked_quietly(child)
        missing = self.child(parent, parent_digest, "child-noauth")
        record = self.record(missing)
        del record["shared_photo_extension"]["authority_reference"]
        self.write(missing, record)
        code, _ = self.run_cli([missing, "--addresses", self.addresses])
        self.assertEqual(code, 4)
        self.assertEqual(len(FakeMunicipality.created), 1)

    def test_second_extension_of_the_same_original_is_refused(self):
        parent, parent_digest, child = self.family()
        code, _, _ = self.submit_child(child)
        self.assertEqual(code, 0)
        second = self.child(parent, parent_digest, "child-c", plate=PLATE_C)
        self.assert_blocked_quietly(second)
        data = self.ledger_data()
        self.assertNotIn(self.key("child-c"), data["reports"])
        self.assertEqual(len(FakeMunicipality.created), 2)

    def test_renamed_child_with_the_same_plate_is_refused(self):
        parent, parent_digest, child = self.family()
        self.assertEqual(self.submit_child(child)[0], 0)
        renamed = self.child(parent, parent_digest, "child-renamed")
        self.assert_blocked_quietly(renamed)
        self.assertEqual(len(FakeMunicipality.created), 2)

    def test_ordinary_dedupe_of_the_same_photo_is_unchanged(self):
        parent, _, child = self.family()
        self.assertEqual(self.submit_child(child)[0], 0)
        plain = self.car("plain", PLATE_C, photo_from=parent)
        code, out, _ = self.submit_ordinary(plain)
        self.assertEqual((code, out), (4, BLOCKED))
        self.assertEqual(len(FakeMunicipality.created), 2)


class ParentEligibilityTests(ExtensionBase):
    def test_parent_without_preserved_evidence_is_ineligible(self):
        self.provision()
        parent = self.car("parent-a", PLATE_A)
        code, out, digest = self.submit_ordinary(parent)
        self.assertEqual((code, out["status"]), (0, "submitted"))
        child = self.child(parent, digest)
        self.assert_blocked_quietly(child)
        self.assertEqual(len(FakeMunicipality.created), 1)

    def test_recorded_digest_alone_never_corroborates(self):
        self.provision()
        parent = self.car("parent-a", PLATE_A)
        code, out, digest = self.submit_ordinary(parent)
        self.assertEqual(code, 0)
        record = self.record(parent)
        record[DIGEST_FIELD] = "0" * 64  # Conflicting recorded digest.
        self.write(parent, record)
        child = self.child(parent, digest)
        self.assert_blocked_quietly(child)
        # A matching string still needs the recomputed original payload.
        record = self.record(parent)
        record[DIGEST_FIELD] = digest
        record["violation"]["description"] = "נוסח שונה"
        self.write(parent, record)
        self.assert_blocked_quietly(child)
        self.assertEqual(len(FakeMunicipality.created), 1)

    def test_parent_record_plate_diverging_from_payload_fails(self):
        parent, parent_digest, child = self.family()
        record = self.record(parent)
        record["plate"] = DASHED[PLATE_C]
        self.write(parent, record)
        self.assert_blocked_quietly(child)
        record = self.record(parent)
        del record["plate"]  # An unknown legacy plate cannot be inferred.
        self.write(parent, record)
        self.assert_blocked_quietly(child)
        self.assertEqual(len(FakeMunicipality.created), 1)

    def test_changed_parent_files_block_a_later_retry(self):
        parent, parent_digest, child = self.family()
        self.assertEqual(self.submit_child(child)[0], 0)
        record = self.record(parent)
        record["plate"] = DASHED[PLATE_C]
        self.write(parent, record)
        code, out, _ = self.submit_child(child)
        self.assertEqual((code, out), (4, BLOCKED))
        self.assertEqual(len(FakeMunicipality.created), 2)
        entry = self.ledger_data()["reports"][self.key("child-b")]
        self.assertEqual(entry["state"]["status"], "complete")

    def test_incomplete_or_missing_parent_is_ineligible(self):
        self.provision()
        parent = self.car("parent-a", PLATE_A)
        digest = self.preview(parent, "--ledger-dir", self.ledger_dir)  # prepared
        record = self.record(parent)
        record[DIGEST_FIELD] = digest
        self.write(parent, record)
        child = self.child(parent, digest)
        self.assert_blocked_quietly(child)
        # A parent reserved nowhere at all is missing, digest guess or not.
        stranger = self.child(parent, digest, "child-z", parent_id="parent-z")
        self.assert_blocked_quietly(stranger)
        self.assertEqual(FakeMunicipality.created, [])

    def test_non_vehicle_parent_is_ineligible(self):
        self.provision()
        subject = {
            "violation": {"category": "graffiti", "description": "גרפיטי על קיר"},
            "municipal_subject": {
                "code": "1072",
                "owner_choice": True,
                "event_type": "graffiti",
            },
            "approved_description": "גרפיטי על קיר ברחוב לדוגמה 1. אבקש לטפל במפגע.",
        }
        parent, digest = self.complete_parent(subject=subject)
        child = self.child(parent, digest)
        self.assert_blocked_quietly(child)
        self.assertEqual(len(FakeMunicipality.created), 1)

    def test_parent_with_followup_or_group_is_ineligible(self):
        parent, parent_digest, child = self.family()
        followup = {
            "parent_id": "parent-a",
            "parent_digest": parent_digest,
            "parent_case_number": "2601-0000001",
            "parent_closed_at": "2026-01-02T12:00:00+02:00",
            "approval_reference": "approval-synthetic-fu",
        }
        with GitLedger(self.ledger_dir).locked() as ledger:
            ledger.reserve("fu-1", "c" * 64, self.photo_hash(parent), followup=followup)
        self.assert_blocked_quietly(child)
        self.assertEqual(len(FakeMunicipality.created), 1)

    def test_parent_outside_the_sibling_layout_is_not_found(self):
        parent, parent_digest, child = self.family()
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        child = child.rename(elsewhere / "child-b")
        self.assert_blocked_quietly(child)
        self.assertEqual(len(FakeMunicipality.created), 1)

    def test_municipal_reference_never_selects_or_upgrades_the_parent(self):
        # The manifest carries no municipal case number: duplicate identity is
        # the parent intake ID, ledger digest and original photo, and a guessed
        # municipal reference cannot join or upgrade anything.
        with self.assertRaises(ValueError):
            validate_extension(
                dict(
                    self.manifest("a" * 64, "b" * 64),
                    parent_case_number="2601-0000001",
                )
            )


class GuardKeyTests(ExtensionBase):
    def test_provision_is_explicit_once_and_never_rekeys(self):
        with GitLedger(self.ledger_dir).locked() as ledger:
            with self.assertRaises(LedgerError):
                ledger.reserve(
                    "child-b",
                    "a" * 64,
                    "b" * 64,
                    extension=self.manifest("c" * 64, "b" * 64),
                    guard=self.guard,
                )
            self.assertNotIn("photo_vehicle", ledger.data)
            ledger.provision_guard_key(self.guard)
            head = ledger.head()
            ledger.provision_guard_key(self.guard)  # idempotent, no new commit
            self.assertEqual(ledger.head(), head)
            for bad in (
                GuardKey("other-id", SEED),
                GuardKey(KEY_ID, OTHER_SEED),
            ):
                with self.subTest(key_id=bad.key_id):
                    with self.assertRaises(LedgerError):
                        ledger.provision_guard_key(bad)

    def test_missing_or_malformed_index_fails_closed(self):
        parent, parent_digest, child = self.family()

        def corrupt(data):
            del data["photo_vehicle"]["key"]["verifier"]

        self.edit_ledger(corrupt)
        self.assert_blocked_quietly(child)
        self.assertEqual(len(FakeMunicipality.created), 1)

    def test_missing_seed_or_key_id_blocks_before_reservation(self):
        _, _, child = self.family()
        before = git(self.ledger_dir, "rev-parse", "HEAD")
        for env in (
            {k: v for k, v in LIVE.items() if k != "PARKING_GUARD_SEED"},
            {k: v for k, v in LIVE.items() if k != "PARKING_GUARD_KEY_ID"},
            dict(LIVE, PARKING_GUARD_SEED="not-hex"),
            dict(LIVE, PARKING_GUARD_SEED="aa" * 8),  # below 128 bits
        ):
            with self.subTest(env=sorted(set(LIVE) - set(env)) or "seed-value"):
                code, out, _ = self.submit_child(child, env=env)
                self.assertEqual((code, out), (4, BLOCKED))
        self.assertEqual(git(self.ledger_dir, "rev-parse", "HEAD"), before)
        self.assertNotIn(self.key("child-b"), self.ledger_data()["reports"])
        self.assertEqual(len(FakeMunicipality.created), 1)

    def test_wrong_seed_or_unknown_key_id_cannot_rekey(self):
        _, _, child = self.family()
        before = git(self.ledger_dir, "rev-parse", "HEAD")
        for env in (
            dict(LIVE, PARKING_GUARD_SEED=OTHER_SEED.hex()),
            dict(LIVE, PARKING_GUARD_KEY_ID="other-id"),
        ):
            with self.subTest():
                code, out, _ = self.submit_child(child, env=env)
                self.assertEqual((code, out), (4, BLOCKED))
        self.assertEqual(git(self.ledger_dir, "rev-parse", "HEAD"), before)
        self.assertEqual(len(FakeMunicipality.created), 1)

    def test_unprovisioned_index_blocks_the_reservation(self):
        parent, parent_digest = self.complete_parent()  # no provision() call
        child = self.child(parent, parent_digest)
        self.assert_blocked_quietly(child)
        self.assertNotIn("photo_vehicle", self.ledger_data())
        self.assertEqual(len(FakeMunicipality.created), 1)

    def test_approval_gate_needs_the_exact_extension_digest(self):
        _, _, child = self.family()
        digest = self.preview(child)
        for approval in ("", "wrong"):
            with self.subTest(approval=approval):
                code, out, _ = self.submit_child(
                    child, approval=approval, digest=digest
                )
                self.assertEqual((code, out), (4, BLOCKED))
        code, out, _ = self.submit_child(child, approval=False, digest=digest)
        self.assertEqual((code, out), (4, BLOCKED))
        self.assertNotIn(self.key("child-b"), self.ledger_data()["reports"])
        self.assertEqual(len(FakeMunicipality.created), 1)

    def test_every_gate_clause_blocks_before_reservation(self):
        _, _, child = self.family()
        digest = self.preview(child)
        for normal, ext, enabled in (
            (digest, "wrong", "true"),
            ("wrong", digest, "true"),
            (digest, digest, "false"),
        ):
            ledger = MagicMock()
            ledger.locked.return_value.__enter__.return_value = ledger
            with (
                self.subTest(normal=normal, ext=ext, enabled=enabled),
                patch("parking_report.ledger.GitLedger", return_value=ledger),
                patch.object(api_cli, "execute", return_value=0),
            ):
                code, _ = self.run_cli(
                    [
                        child,
                        "--addresses",
                        self.addresses,
                        "--ledger-dir",
                        self.ledger_dir,
                        "--submit",
                        "--approved-sha256",
                        normal,
                        "--approved-extension-sha256",
                        ext,
                    ],
                    dict(GUARD_ENV, PARKING_106_ENABLE_LIVE=enabled),
                )
                self.assertEqual(code, 4)
                ledger.reserve.assert_not_called()


class RetryAndUncertaintyTests(ExtensionBase):
    def test_same_child_retry_resumes_its_receipt(self):
        parent, parent_digest, child = self.family()
        code, out, digest = self.submit_child(child)
        self.assertEqual((code, out["status"]), (0, "submitted"))
        case = self.ledger_data()["reports"][self.key("child-b")]["state"]["case"]
        code, again, _ = self.submit_child(child, digest=digest)
        self.assertEqual((code, again["status"]), (0, "already_submitted"))
        self.assertEqual(again["sha256"], digest)
        self.assertEqual(len(FakeMunicipality.created), 2)
        data = self.ledger_data()
        self.assertEqual(data["reports"][self.key("child-b")]["state"]["case"], case)

    def test_changed_relation_on_retry_fails_closed(self):
        parent, parent_digest, child = self.family()
        self.assertEqual(self.submit_child(child)[0], 0)
        record = self.record(child)
        record["shared_photo_extension"]["authority_reference"] = "approval-other-9"
        self.write(child, record)
        self.assert_blocked_quietly(child)
        self.assertEqual(len(FakeMunicipality.created), 2)

    def test_uncertain_create_blocks_without_repeating_parent_or_child(self):
        parent, parent_digest, child = self.family()
        FakeMunicipality.failing = {PLATE_B}
        self.assertEqual(self.submit_child(child)[0], 4)
        entry = self.ledger_data()["reports"][self.key("child-b")]
        self.assertEqual(entry["state"]["status"], "creating")
        FakeMunicipality.failing = set()
        self.assertEqual(self.submit_child(child)[0], 4)  # no automatic retry
        self.assertEqual(len(FakeMunicipality.created), 1)  # parent only
        parent_entry = self.ledger_data()["reports"][self.key("parent-a")]
        self.assertEqual(parent_entry["state"]["status"], "complete")

    def test_stale_writer_blocks_the_effect(self):
        parent, parent_digest, child = self.family()
        stale = self.root / "stale-clone"
        subprocess.run(
            ["git", "clone", str(self.root / "remote-0.git"), str(stale)],
            capture_output=True,
            check=True,
        )
        git(stale, "config", "user.name", "Test")
        git(stale, "config", "user.email", "test@example.invalid")
        git(stale, "checkout", "106-ledger")
        # The real ledger advances after the stale clone was taken.
        self.edit_ledger(lambda data: data.setdefault("operator_note", "advance"))
        argv_digest = self.preview(child)
        saved = self.ledger_dir
        self.ledger_dir = stale
        try:
            code, out, _ = self.submit_child(child, digest=argv_digest)
        finally:
            self.ledger_dir = saved
        self.assertEqual((code, out), (4, BLOCKED))
        self.assertEqual(len(FakeMunicipality.created), 1)


class NonRegularParentTests(ExtensionBase):
    """Missing/FIFO/device parent records fail promptly, never hang (#284)."""

    def _corroborate_promptly(self, folder, record, manifest, data):
        outcome: list[BaseException | None] = []

        def run():
            try:
                corroborate_parent(
                    folder, record, manifest, data, self.addresses, REPORTER_ENV
                )
                outcome.append(None)
            except BaseException as exc:  # noqa: BLE001 - recorded for assertion
                outcome.append(exc)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(timeout=5)
        if worker.is_alive():
            target = folder.parent / manifest["parent_id"] / "report.json"
            if target.is_fifo():
                fd = os.open(target, os.O_WRONLY | os.O_NONBLOCK)
                os.close(fd)
            worker.join(timeout=5)
            self.fail("parent corroboration blocked on a non-regular record")
        return outcome[0]

    def test_missing_fifo_and_device_parent_records_fail_promptly(self):
        parent, parent_digest, child = self.family()
        record = self.record(child)
        manifest = record["shared_photo_extension"]
        data = self.ledger_data()
        target = parent / "report.json"
        saved = target.read_bytes()
        try:
            target.unlink()
            error = self._corroborate_promptly(child, record, manifest, data)
            self.assertIsInstance(error, ValueError)
            if hasattr(os, "mkfifo"):
                os.mkfifo(target)
                error = self._corroborate_promptly(child, record, manifest, data)
                self.assertIsInstance(error, ValueError)
                target.unlink()
            if os.path.exists("/dev/null"):
                target.symlink_to("/dev/null")
                error = self._corroborate_promptly(child, record, manifest, data)
                self.assertIsInstance(error, ValueError)
        finally:
            if target.is_symlink() or target.is_fifo():
                target.unlink()
            if not target.exists():
                target.write_bytes(saved)

    def test_invalid_parent_record_json_fails(self):
        parent, parent_digest, child = self.family()
        record = self.record(child)
        manifest = record["shared_photo_extension"]
        data = self.ledger_data()
        target = parent / "report.json"
        saved = target.read_bytes()
        try:
            for raw in (b'{"plate": ', b"\xff\xfe\x00", b"[]", b""):
                with self.subTest(raw=raw[:8]):
                    target.write_bytes(raw)
                    error = self._corroborate_promptly(child, record, manifest, data)
                    self.assertIsInstance(error, ValueError)
        finally:
            target.write_bytes(saved)


REPORTER_ENV = {
    "first_name": "דנה",
    "last_name": "לוי",
    "phone": SYNTHETIC_PHONE,
    "id_number": SYNTHETIC_ID,
    "email": "dana@example.com",
}


class PrivacyTests(ExtensionBase):
    def test_no_plate_or_seed_reaches_ledger_logs_or_output(self):
        parent, parent_digest, child = self.family()
        self.submit_child(child)
        self.submit_child(child)  # retry path too
        self.assert_blocked_quietly(
            self.child(parent, parent_digest, "child-c", plate=PLATE_C)
        )
        texts = [self.ledger_text(), git(self.ledger_dir, "log", "-p"), *self.outputs]
        for plate in (PLATE_A, PLATE_B, PLATE_C):
            for form in (plate, DASHED[plate]):
                for text in texts:
                    self.assertNotIn(form, text)
        for text in texts:
            self.assertNotIn(SEED.hex(), text)
            self.assertNotIn(OTHER_SEED.hex(), text)
        # Derived keys live only in the private index, not in any relation.
        entry = self.ledger_data()["reports"][self.key("child-b")]
        self.assertNotIn(
            self.guard.identity(self.photo_hash(parent), PLATE_B),
            json.dumps(entry["extension"]),
        )

    def test_legacy_ledger_entries_stay_untouched(self):
        self.provision()
        parent, parent_digest = self.complete_parent()
        before = json.loads(json.dumps(self.ledger_data()))
        child = self.child(parent, parent_digest)
        self.assertEqual(self.submit_child(child)[0], 0)
        data = self.ledger_data()
        self.assertEqual(
            data["reports"][self.key("parent-a")],
            before["reports"][self.key("parent-a")],
        )
        self.assertEqual(data["photos"], before["photos"])
        # A legacy parent never gains a guard entry for its own plate.
        photo = self.photo_hash(parent)
        self.assertNotIn(
            self.guard.identity(photo, PLATE_A), data["photo_vehicle"]["guards"]
        )


if __name__ == "__main__":
    unittest.main()
