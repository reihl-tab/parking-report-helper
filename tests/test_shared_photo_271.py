"""Explicit shared-photo manifest (#271): two distinct cars, one original photo.

Synthetic fixtures only: fake plates, generated images, fake case receipts and
temporary local Git ledgers. No network.
"""

from __future__ import annotations

import hashlib
import inspect
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

from PIL import Image

from parking_report import api_cli
from parking_report.ledger import GitLedger, LedgerError
from parking_report.shared_photo import (
    _INTAKE_ID,
    GROUP_SIZE,
    canonical_manifest,
    check_sibling,
    group_fingerprint,
    sibling_record_path,
    validate_shared_photo,
)

ENV = {
    "PARKING_REPORTER_FIRST_NAME": "דנה",
    "PARKING_REPORTER_LAST_NAME": "לוי",
    "PARKING_REPORTER_PHONE": "0500000000",
    "PARKING_REPORTER_ID_NUMBER": "000000018",
    "PARKING_REPORTER_EMAIL": "dana@example.com",
}
LIVE = dict(ENV, PARKING_106_ENABLE_LIVE="true")
# Fake plates: the records use the dashed form, manifests the normalized digits.
PLATE_A, PLATE_B, PLATE_C = "1000001", "20000002", "3000003"
DASHED = {PLATE_A: "10-000-01", PLATE_B: "200-00-002", PLATE_C: "30-000-03"}
REFERENCE = "approval-synthetic-271"


class Out(io.StringIO):
    def reconfigure(self, encoding=None):
        pass


class FakeMunicipality:
    """Counts creations; a creation for a listed plate fails (uncertain)."""

    created: ClassVar[list[str]] = []
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


class SharedPhotoBase(unittest.TestCase):
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
        FakeMunicipality.failing = set()

    def make_ledger(self):
        remote = self.root / "remote.git"
        subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True)
        clone = self.root / "ledger"
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

    def ledger_text(self):
        return (self.ledger_dir / "ledger.json").read_text(encoding="utf-8")

    def ledger_data(self):
        return json.loads(self.ledger_text())

    def car(self, intake_id, plate, color="white", description=None):
        folder = self.intakes / intake_id
        folder.mkdir()
        Image.new("RGB", (10, 10), color).save(folder / "photo.jpg")
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
        if description is not None:
            record["approved_description"] = description
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

    def manifest(self, photo_hash, ids=("car-a", "car-b"), plates=(PLATE_A, PLATE_B)):
        return {
            "intake_ids": list(ids),
            "plates": list(plates),
            "photo_sha256": photo_hash,
            "authority_reference": REFERENCE,
        }

    def attach(self, folder, manifest, **extra):
        record = self.record(folder)
        record["shared_photo"] = manifest
        record.update(extra)
        self.write(folder, record)

    def pair(self, **kwargs):
        a = self.car("car-a", PLATE_A, **kwargs)
        b = self.car("car-b", PLATE_B, **kwargs)
        manifest = self.manifest(self.photo_hash(a))
        self.attach(a, manifest)
        self.attach(b, manifest)
        return a, b, manifest

    def run_cli(self, argv, env=ENV):
        out, err = Out(), Out()
        with (
            patch.object(sys, "stdout", out),
            patch.object(sys, "stderr", err),
            patch.dict("os.environ", env, clear=False),
        ):
            code = api_cli.main([str(a) for a in argv])
        self.outputs += [out.getvalue(), err.getvalue()]
        return code, out.getvalue()

    def preview(self, folder, *extra):
        code, out = self.run_cli([folder, "--addresses", self.addresses, *extra])
        self.assertEqual(code, 0, out)
        return json.loads(out)["sha256"]

    def submit(self, folder, approval=None, *, digest=None, env=LIVE):
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
        ]
        if approval is not False:
            argv += ["--approved-shared-photo-sha256", approval or digest]
        with (
            patch("parking_report.api106.Municipality", FakeMunicipality),
            patch.object(api_cli, "post_submit_duplicate_check", return_value=[]),
        ):
            code, out = self.run_cli(argv, env)
        return code, json.loads(out)

    def key(self, intake_id):
        return hashlib.sha256(intake_id.encode()).hexdigest()


class ManifestValidationTests(SharedPhotoBase):
    def test_exact_manifest_and_every_rejection(self):
        good = self.manifest("b" * 64)
        self.assertEqual(validate_shared_photo(good), good)
        for name, bad in (
            ("not an object", None),
            ("missing field", {k: v for k, v in good.items() if k != "plates"}),
            ("extra field", dict(good, extra="x")),
            ("repeated intake ID", dict(good, intake_ids=["car-a", "car-a"])),
            ("unsorted intake IDs", dict(good, intake_ids=["car-b", "car-a"])),
            ("unsafe intake ID", dict(good, intake_ids=["../a", "car-b"])),
            ("same car twice", dict(good, plates=[PLATE_A, PLATE_A])),
            ("plate not normalized", dict(good, plates=[DASHED[PLATE_A], PLATE_B])),
            ("non-standard plate", dict(good, plates=["123456", PLATE_B])),
            ("letters in plate", dict(good, plates=["12א34567", PLATE_B])),
            ("plates not a list", dict(good, plates=PLATE_A)),
            ("bad photo hash", dict(good, photo_sha256="B" * 64)),
            ("blank authority", dict(good, authority_reference=" ")),
            ("authority not text", dict(good, authority_reference=1)),
        ):
            with self.subTest(name), self.assertRaises(ValueError):
                validate_shared_photo(bad)

    def test_group_is_bounded_to_exactly_two(self):
        self.assertEqual(GROUP_SIZE, 2)
        three = self.manifest(
            "b" * 64,
            ids=("car-a", "car-b", "car-c"),
            plates=(PLATE_A, PLATE_B, PLATE_C),
        )
        one = self.manifest("b" * 64, ids=("car-a",), plates=(PLATE_A,))
        for bad in (three, one):
            with self.subTest(size=len(bad["intake_ids"])):
                with self.assertRaises(ValueError):
                    validate_shared_photo(bad)
                with GitLedger(self.ledger_dir).locked() as ledger:
                    with self.assertRaises(ValueError):
                        ledger.reserve("car-a", "a" * 64, "b" * 64, shared_photo=bad)

    def test_three_member_records_are_blocked_at_the_cli(self):
        folders = [
            self.car(f"car-{n}", plate)
            for n, plate in zip("abc", (PLATE_A, PLATE_B, PLATE_C), strict=True)
        ]
        three = self.manifest(
            self.photo_hash(folders[0]),
            ids=("car-a", "car-b", "car-c"),
            plates=(PLATE_A, PLATE_B, PLATE_C),
        )
        for folder in folders:
            self.attach(folder, three)
            code, _ = self.run_cli([folder, "--addresses", self.addresses])
            self.assertEqual(code, 4)

    def test_group_fingerprint_leaves_plates_out(self):
        good = self.manifest("b" * 64)
        other = dict(good, plates=[PLATE_C, PLATE_B])
        self.assertEqual(group_fingerprint(good), group_fingerprint(other))
        self.assertNotEqual(
            group_fingerprint(good),
            group_fingerprint(dict(good, authority_reference="approval-other")),
        )


class SharedPhotoFlowTests(SharedPhotoBase):
    def test_two_distinct_cars_from_one_photo_are_both_submitted(self):
        a, b, manifest = self.pair()
        code_a, out_a = self.submit(a)
        code_b, out_b = self.submit(b)
        self.assertEqual((code_a, code_b), (0, 0))
        self.assertEqual((out_a["status"], out_b["status"]), ("submitted",) * 2)
        self.assertNotEqual(out_a["sha256"], out_b["sha256"])
        self.assertEqual(len(FakeMunicipality.created), 2)
        data = self.ledger_data()
        group = group_fingerprint(manifest)
        photo = manifest["photo_sha256"]
        for intake, out in (("car-a", out_a), ("car-b", out_b)):
            entry = data["reports"][self.key(intake)]
            self.assertEqual(entry["shared_photo"], group)
            self.assertEqual(entry["digest"], out["sha256"])
            self.assertEqual(entry["state"]["status"], "complete")
        self.assertNotEqual(
            data["reports"][self.key("car-a")]["state"]["case"],
            data["reports"][self.key("car-b")]["state"]["case"],
        )
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["photos"], {photo: self.key("car-a")})
        self.assertEqual(
            data["shared_photos"],
            {
                photo: {
                    "group": group,
                    "members": [self.key("car-a"), self.key("car-b")],
                }
            },
        )

    def test_identical_text_still_gives_each_car_its_own_digest(self):
        a, b, _ = self.pair(description="רכב חונה על המדרכה ברחוב לדוגמה 1.")
        self.assertNotEqual(self.preview(a), self.preview(b))

    def test_same_car_twice_is_rejected(self):
        a = self.car("car-a", PLATE_A)
        b = self.car("car-b", PLATE_A)
        same = self.manifest(self.photo_hash(a), plates=(PLATE_A, PLATE_A))
        self.attach(a, same)
        self.attach(b, same)
        for folder in (a, b):
            self.assertEqual(
                self.run_cli([folder, "--addresses", self.addresses])[0], 4
            )
        # A manifest that claims distinct plates cannot hide the same car.
        claimed = self.manifest(self.photo_hash(a))
        self.attach(a, claimed)
        self.attach(b, claimed)
        self.assertEqual(self.submit(a)[0], 0)
        self.assertEqual(self.run_cli([b, "--addresses", self.addresses])[0], 4)
        self.assertNotIn(self.key("car-b"), self.ledger_data()["reports"])

    def test_non_standard_plate_cannot_join(self):
        a = self.car("car-a", "12א-345-67")
        manifest = self.manifest(self.photo_hash(a), plates=("1234567", PLATE_B))
        self.attach(a, manifest)
        self.assertEqual(self.run_cli([a, "--addresses", self.addresses])[0], 4)

    def test_manifest_and_intake_mismatch_is_rejected(self):
        a, b, manifest = self.pair()
        self.assertEqual(self.submit(a)[0], 0)
        # b's record names another partner than the locked group.
        c_group = self.manifest(manifest["photo_sha256"], ids=("car-b", "car-c"))
        c_group["plates"] = [PLATE_B, PLATE_C]
        self.attach(b, c_group)
        self.assertEqual(self.submit(b)[0], 4)
        # A record whose own ID is not in its manifest never reaches the ledger.
        stranger = self.car("car-z", PLATE_C)
        self.attach(stranger, manifest)
        self.assertEqual(self.run_cli([stranger, "--addresses", self.addresses])[0], 4)
        self.assertEqual(set(self.ledger_data()["reports"]), {self.key("car-a")})

    def test_unrelated_photo_hash_is_rejected(self):
        a = self.car("car-a", PLATE_A)
        b = self.car("car-b", PLATE_B, color="black")
        manifest = self.manifest(self.photo_hash(a))
        self.attach(a, manifest)
        self.attach(b, manifest)
        self.assertEqual(self.submit(a)[0], 0)
        self.assertEqual(self.run_cli([b, "--addresses", self.addresses])[0], 4)
        with (
            GitLedger(self.ledger_dir).locked() as ledger,
            self.assertRaises(LedgerError),
        ):
            ledger.reserve("car-b", "c" * 64, self.photo_hash(b), shared_photo=manifest)
        self.assertNotIn(self.key("car-b"), self.ledger_data()["reports"])

    def test_missing_approval_flag_blocks_before_reservation(self):
        a, b, _ = self.pair()
        self.assertEqual(self.submit(a)[0], 0)
        before = git(self.ledger_dir, "rev-parse", "HEAD")
        self.assertEqual(self.submit(b, approval=False)[0], 4)
        # Approving with the sibling's digest is not approval either.
        sibling = self.preview(a)
        self.assertEqual(self.submit(b, approval=sibling)[0], 4)
        self.assertEqual(git(self.ledger_dir, "rev-parse", "HEAD"), before)
        # Without a manifest the second intake meets ordinary photo dedupe.
        record = self.record(b)
        record.pop("shared_photo")
        self.write(b, record)
        code, _ = self.submit(b, approval=False)
        self.assertEqual(code, 4)
        self.assertNotIn(self.key("car-b"), self.ledger_data()["reports"])
        self.assertEqual(len(FakeMunicipality.created), 1)

    def test_every_approval_clause_blocks_before_reservation(self):
        a, _, _ = self.pair()
        digest = self.preview(a)
        for normal, shared, enabled, has_manifest in (
            (digest, "wrong", "true", True),
            (digest, "", "true", True),
            ("wrong", digest, "true", True),
            (digest, digest, "false", True),
            (digest, digest, "true", False),
        ):
            if not has_manifest:
                record = self.record(a)
                record.pop("shared_photo")
                self.write(a, record)
            ledger = MagicMock()
            ledger.locked.return_value.__enter__.return_value = ledger
            with (
                self.subTest(normal=normal, shared=shared, enabled=enabled),
                patch("parking_report.ledger.GitLedger", return_value=ledger),
                patch.object(api_cli, "execute", return_value=0),
            ):
                code, _ = self.run_cli(
                    [
                        a,
                        "--addresses",
                        self.addresses,
                        "--ledger-dir",
                        self.ledger_dir,
                        "--submit",
                        "--approved-sha256",
                        normal,
                        "--approved-shared-photo-sha256",
                        shared,
                    ],
                    dict(ENV, PARKING_106_ENABLE_LIVE=enabled),
                )
                self.assertEqual(code, 4)
                ledger.reserve.assert_not_called()

    def test_explicit_gate_passes_manifest_to_ledger(self):
        a, _, manifest = self.pair()
        digest = self.preview(a)
        ledger = MagicMock()
        ledger.locked.return_value.__enter__.return_value = ledger
        with (
            patch("parking_report.ledger.GitLedger", return_value=ledger),
            patch.object(api_cli, "execute", return_value=0) as execute,
        ):
            code, _ = self.run_cli(
                [
                    a,
                    "--addresses",
                    self.addresses,
                    "--ledger-dir",
                    self.ledger_dir,
                    "--submit",
                    "--approved-sha256",
                    digest,
                    "--approved-shared-photo-sha256",
                    digest,
                ],
                LIVE,
            )
        self.assertEqual(code, 0)
        self.assertEqual(ledger.reserve.call_args.kwargs["shared_photo"], manifest)
        execute.assert_called_once()

    def test_retries_return_each_stored_receipt(self):
        a, b, _ = self.pair()
        first_a, first_b = self.submit(a)[1], self.submit(b)[1]
        cases = {
            k: self.ledger_data()["reports"][self.key(k)]["state"]["case"]
            for k in ("car-a", "car-b")
        }
        for folder, first in ((a, first_a), (b, first_b)):
            code, again = self.submit(folder)
            self.assertEqual(code, 0)
            self.assertEqual(again["status"], "already_submitted")
            self.assertEqual(again["sha256"], first["sha256"])
        self.assertEqual(len(FakeMunicipality.created), 2)
        for k, case in cases.items():
            self.assertEqual(
                self.ledger_data()["reports"][self.key(k)]["state"]["case"], case
            )

    def test_uncertain_member_stays_refused_and_sibling_proceeds(self):
        a, b, _ = self.pair()
        FakeMunicipality.failing = {PLATE_A}
        self.assertEqual(self.submit(a)[0], 4)
        reports = self.ledger_data()["reports"]
        self.assertEqual(reports[self.key("car-a")]["state"]["status"], "creating")
        FakeMunicipality.failing = set()
        self.assertEqual(self.submit(a)[0], 4)  # no automatic retry
        self.assertEqual(FakeMunicipality.created, [])
        code, out = self.submit(b)
        self.assertEqual((code, out["status"]), (0, "submitted"))
        reports = self.ledger_data()["reports"]
        self.assertEqual(reports[self.key("car-a")]["state"]["status"], "creating")
        self.assertEqual(reports[self.key("car-b")]["state"]["status"], "complete")

    def test_preview_reserves_nothing(self):
        a, b, _ = self.pair()
        before = git(self.ledger_dir, "rev-parse", "HEAD")
        for folder in (a, b):
            self.preview(folder, "--ledger-dir", self.ledger_dir)
        self.assertEqual(git(self.ledger_dir, "rev-parse", "HEAD"), before)
        self.assertEqual(self.ledger_data()["reports"], {})

    def test_followup_and_shared_photo_together_are_rejected(self):
        a, _, manifest = self.pair()
        followup = {
            "parent_id": "original",
            "parent_digest": "a" * 64,
            "parent_case_number": "2601-1234567",
            "parent_closed_at": "2026-01-01T12:00:00+02:00",
            "approval_reference": "approval-synthetic-1",
        }
        self.attach(a, manifest, followup=followup)
        self.assertEqual(self.run_cli([a, "--addresses", self.addresses])[0], 4)
        with (
            GitLedger(self.ledger_dir).locked() as ledger,
            self.assertRaises(LedgerError),
        ):
            ledger.reserve(
                "car-a",
                "a" * 64,
                manifest["photo_sha256"],
                followup=followup,
                shared_photo=manifest,
            )

    def test_no_plate_reaches_ledger_or_output(self):
        a, b, _ = self.pair()
        self.submit(a)
        self.submit(b)
        self.submit(b)
        self.run_cli([self.car("car-c", PLATE_C), "--addresses", self.addresses])
        texts = [self.ledger_text(), git(self.ledger_dir, "log", "-p"), *self.outputs]
        for plate in (PLATE_A, PLATE_B, PLATE_C):
            for form in (plate, DASHED[plate]):
                for text in texts:
                    self.assertNotIn(form, text)


class GroupLockTests(SharedPhotoBase):
    """The group locks at the first reservation (E1) and never changes later (E2)."""

    def setUp(self):
        super().setUp()
        self.manifest_ab = self.manifest("b" * 64)

    def reserve(self, ledger, intake, manifest, digest="a"):
        return ledger.reserve(intake, digest * 64, "b" * 64, shared_photo=manifest)

    def test_group_locks_at_first_reservation(self):
        changed = dict(self.manifest_ab, authority_reference="approval-other")
        other_partner = self.manifest(
            "b" * 64, ids=("car-a", "car-c"), plates=(PLATE_A, PLATE_C)
        )
        with GitLedger(self.ledger_dir).locked() as ledger:
            self.reserve(ledger, "car-a", self.manifest_ab)
            index = json.loads(json.dumps(ledger.data["shared_photos"]))
            for intake, manifest in (
                ("car-b", changed),
                ("car-c", other_partner),
                ("car-a", changed),
            ):
                with self.subTest(intake=intake), self.assertRaises(LedgerError):
                    self.reserve(ledger, intake, manifest, digest="c")
            self.assertEqual(ledger.data["shared_photos"], index)
            entry = self.reserve(ledger, "car-b", self.manifest_ab, digest="d")
            self.assertEqual(entry["state"]["status"], "prepared")

    def test_nothing_changes_after_first_car_leaves_prepared(self):
        changed = dict(self.manifest_ab, authority_reference="approval-other")
        with GitLedger(self.ledger_dir).locked() as ledger:
            self.reserve(ledger, "car-a", self.manifest_ab)
            for status in ("creating", "complete"):
                ledger.checkpoint("car-a", {"version": 1, "status": status})
                snapshot = json.loads(json.dumps(ledger.data))
                for intake, manifest in (
                    ("car-a", changed),
                    ("car-b", changed),
                    ("car-a", None),
                ):
                    with (
                        self.subTest(status=status, intake=intake),
                        self.assertRaises(LedgerError),
                    ):
                        self.reserve(ledger, intake, manifest, digest="e")
                self.assertEqual(ledger.data, snapshot)
            self.assertEqual(
                self.reserve(ledger, "car-a", self.manifest_ab)["state"]["status"],
                "complete",
            )
            self.reserve(ledger, "car-b", self.manifest_ab, digest="d")
            with self.assertRaises(LedgerError):
                self.reserve(ledger, "car-c", self.manifest_ab, digest="f")

    def test_reserved_ordinary_intake_cannot_join_later(self):
        a, b, manifest = self.pair()
        record = self.record(a)
        record.pop("shared_photo")
        self.write(a, record)
        self.preview(a, "--ledger-dir", self.ledger_dir)  # ordinary preview reserves
        self.attach(a, manifest)
        self.assertEqual(self.submit(a)[0], 4)
        self.assertEqual(self.submit(b)[0], 4)
        data = self.ledger_data()
        self.assertEqual(set(data["reports"]), {self.key("car-a")})
        self.assertNotIn("shared_photo", data["reports"][self.key("car-a")])
        self.assertNotIn("shared_photos", data)
        self.assertEqual(FakeMunicipality.created, [])


class OrdinaryDedupeTests(SharedPhotoBase):
    def test_ordinary_duplicate_photo_is_still_blocked(self):
        with GitLedger(self.ledger_dir).locked() as ledger:
            ledger.reserve("one", "a" * 64, "b" * 64)
            with self.assertRaises(LedgerError):
                ledger.reserve("two", "c" * 64, "b" * 64)
            self.assertEqual(ledger.data["photos"], {"b" * 64: self.key("one")})

    def test_ordinary_intake_cannot_reuse_a_group_photo(self):
        a, b, manifest = self.pair()
        self.submit(a)
        self.submit(b)
        c = self.car("car-c", PLATE_C)
        self.assertEqual(self.photo_hash(c), manifest["photo_sha256"])
        self.assertEqual(self.submit(c, approval=False)[0], 4)
        with (
            GitLedger(self.ledger_dir).locked() as ledger,
            self.assertRaises(LedgerError),
        ):
            ledger.reserve("car-c", "c" * 64, manifest["photo_sha256"])
        self.assertNotIn(self.key("car-c"), self.ledger_data()["reports"])
        self.assertEqual(len(FakeMunicipality.created), 2)


BLOCKED = {
    "status": "blocked",
    "note": "Check private input and ledger; no automatic retry.",
}


class SiblingCheckTests(SharedPhotoBase):
    """Fail closed: a member reserves only beside a sibling with the same manifest.

    The ledger is plate-free (C1), so it cannot compare plates across members.
    Each reservation reads <parent>/<sibling intake ID>/report.json instead.
    """

    def assert_refused(self, *folders):
        for folder in folders:
            code, out = self.submit(folder)
            self.assertEqual(code, 4, folder.name)
            self.assertEqual(out, BLOCKED)
        self.assertEqual(self.ledger_data()["reports"], {})
        self.assertNotIn("shared_photos", self.ledger_data())
        self.assertEqual(FakeMunicipality.created, [])
        for text in [*self.outputs, self.ledger_text()]:
            for plate in (PLATE_A, PLATE_B, PLATE_C):
                self.assertNotIn(plate, text)
                self.assertNotIn(DASHED[plate], text)

    def test_divergent_plate_lists_sharing_a_plate_refuse_both_cars(self):
        # The same car twice, hidden behind two different "distinct" plate lists.
        a = self.car("car-a", PLATE_A)
        b = self.car("car-b", PLATE_A)
        photo = self.photo_hash(a)
        self.attach(a, self.manifest(photo, plates=(PLATE_A, PLATE_B)))
        self.attach(b, self.manifest(photo, plates=(PLATE_C, PLATE_A)))
        # Each manifest alone is consistent with its own record.
        self.preview(a)
        self.preview(b)
        self.assert_refused(a, b)
        self.assert_refused(b, a)

    def test_missing_sibling_folder_refuses_the_first_member(self):
        a = self.car("car-a", PLATE_A)
        manifest = self.manifest(self.photo_hash(a))
        self.attach(a, manifest)
        self.assert_refused(a)
        # Once the sibling sits beside it with the same manifest, both proceed.
        b = self.car("car-b", PLATE_B)
        self.attach(b, manifest)
        self.assertEqual(self.submit(a)[0], 0)
        self.assertEqual(self.submit(b)[0], 0)
        self.assertEqual(len(FakeMunicipality.created), 2)

    def test_sibling_in_another_parent_folder_is_not_found(self):
        a = self.car("car-a", PLATE_A)
        b = self.car("car-b", PLATE_B)
        manifest = self.manifest(self.photo_hash(a))
        self.attach(a, manifest)
        self.attach(b, manifest)
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        b = b.rename(elsewhere / "car-b")
        self.assert_refused(a, b)

    def test_sibling_folder_without_report_refuses(self):
        a = self.car("car-a", PLATE_A)
        self.attach(a, self.manifest(self.photo_hash(a)))
        (self.intakes / "car-b").mkdir()
        self.assert_refused(a)

    def test_unreadable_or_invalid_sibling_report_refuses(self):
        cases = {
            "truncated JSON": b'{"shared_photo": ',
            "not UTF-8": b"\xff\xfe\x00",
            "JSON list": b"[]",
            "JSON string": b'"shared_photo"',
            "empty file": b"",
        }
        a, b, _ = self.pair()
        for name, raw in cases.items():
            with self.subTest(name):
                (b / "report.json").write_bytes(raw)
                self.assert_refused(a)
        with self.subTest("report.json is a directory"):
            (b / "report.json").unlink()
            (b / "report.json").mkdir()
            self.assert_refused(a)

    def test_sibling_without_shared_photo_refuses(self):
        a, b, _ = self.pair()
        record = self.record(b)
        del record["shared_photo"]
        self.write(b, record)
        self.assert_refused(a)

    def test_manifest_mismatch_refuses(self):
        a, b, manifest = self.pair()
        cases = {
            "authority reference": dict(manifest, authority_reference="other-ref-1"),
            "plate order": dict(manifest, plates=[PLATE_B, PLATE_A]),
            "other plate": dict(manifest, plates=[PLATE_A, PLATE_C]),
            "photo hash": dict(manifest, photo_sha256="0" * 64),
            "intake IDs": dict(manifest, intake_ids=["car-a", "car-c"]),
            "extra field": dict(manifest, note="x"),
            "invalid manifest": dict(manifest, plates=[PLATE_A, PLATE_A]),
            "null manifest": None,
        }
        for name, sibling_manifest in cases.items():
            with self.subTest(name):
                self.attach(b, sibling_manifest)
                self.assert_refused(a)

    def test_mismatch_refuses_whichever_member_reserves(self):
        a, b, manifest = self.pair()
        self.attach(b, dict(manifest, authority_reference="other-ref-1"))
        self.assert_refused(b, a)

    def test_key_order_in_the_sibling_file_does_not_matter(self):
        # Manifests compare as canonical bytes (sorted keys, no whitespace).
        a, b, manifest = self.pair()
        self.attach(b, dict(reversed(list(manifest.items()))))
        self.assertNotEqual(
            json.dumps(self.record(a)["shared_photo"]),
            json.dumps(self.record(b)["shared_photo"]),
        )
        self.assertEqual(
            canonical_manifest(validate_shared_photo(self.record(b)["shared_photo"])),
            canonical_manifest(validate_shared_photo(manifest)),
        )
        self.assertEqual(self.submit(a)[0], 0)
        self.assertEqual(self.submit(b)[0], 0)

    def test_retry_also_needs_the_sibling(self):
        a, b, _ = self.pair()
        self.assertEqual(self.submit(a)[0], 0)
        self.assertEqual(self.submit(b)[0], 0)
        before = self.ledger_text()
        (b / "report.json").unlink()
        code, out = self.submit(a)
        self.assertEqual((code, out), (4, BLOCKED))
        self.assertEqual(self.ledger_text(), before)
        self.assertEqual(len(FakeMunicipality.created), 2)

    def test_preview_does_not_read_the_sibling(self):
        a = self.car("car-a", PLATE_A)
        self.attach(a, self.manifest(self.photo_hash(a)))
        self.preview(a)
        self.assertEqual(self.ledger_data()["reports"], {})

    def test_happy_path_accepts_both_distinct_cars(self):
        a, b, _ = self.pair()
        self.assertEqual(self.submit(a)[0], 0)
        self.assertEqual(self.submit(b)[0], 0)
        reports = self.ledger_data()["reports"]
        self.assertEqual(set(reports), {self.key("car-a"), self.key("car-b")})
        self.assertEqual(len(FakeMunicipality.created), 2)


class SiblingPathTests(unittest.TestCase):
    def manifest(self, ids):
        return {
            "intake_ids": list(ids),
            "plates": [PLATE_A, PLATE_B],
            "photo_sha256": "a" * 64,
            "authority_reference": REFERENCE,
        }

    def test_sibling_is_beside_the_reserving_folder(self):
        folder = Path("/intakes/car-a")
        self.assertEqual(
            sibling_record_path(folder, self.manifest(["car-a", "car-b"])),
            Path("/intakes/car-b/report.json"),
        )

    def test_traversal_sibling_ids_are_rejected(self):
        folder = Path("/intakes/car-a")
        for bad in ("../x", "..", ".", "a/b", "/abs", "x\\y", "", "-x", "x" * 121):
            with self.subTest(bad=bad):
                ids = sorted(["car-a", bad])
                with self.assertRaises(ValueError):
                    sibling_record_path(folder, self.manifest(ids))
                with self.assertRaises(ValueError):
                    validate_shared_photo(self.manifest(ids))

    def test_reserving_folder_must_be_named_by_the_manifest(self):
        with self.assertRaises(ValueError):
            sibling_record_path(Path("/i/car-z"), self.manifest(["car-a", "car-b"]))

    def test_sibling_id_rule_is_the_ledger_rule(self):
        source = inspect.getsource(GitLedger.reserve)
        self.assertIn(f'r"{_INTAKE_ID.pattern}"', source)

    def test_traversal_member_is_blocked_end_to_end(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        folder = root / "intakes" / "car-a"
        folder.mkdir(parents=True)
        outside = root / "x"
        outside.mkdir()
        manifest = self.manifest(["../x", "car-a"])
        (outside / "report.json").write_text(json.dumps({"shared_photo": manifest}))
        with self.assertRaises(ValueError):
            check_sibling(folder, manifest)


if __name__ == "__main__":
    unittest.main()
