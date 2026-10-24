"""Subject selection, non-parking drafts and offline API end-to-end tests."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from parking_report.api_cli import prepare, execute
from parking_report.intake import record_to_car
from parking_report.report import build_report
from parking_report.subjects import load_catalog, select_subject, validate_owner_choice


def record(code="1072", event="graffiti"):
    return {
        "schema_version": 1,
        "status": "ready",
        "photo": "photo.jpg",
        "captured_at": "2026-01-01 12:00",
        "address": {"street": "רחוב לדוגמה", "house_number": "1"},
        "violation": {"category": event, "description": "גרפיטי על קיר"},
        "municipal_subject": {"code": code, "owner_choice": True, "event_type": event},
        "approved_description": "גרפיטי על קיר ברחוב לדוגמה 1. אבקש לטפל במפגע.",
    }


class SubjectTests(unittest.TestCase):
    def test_catalog_preserves_duplicate_paths(self):
        catalog = load_catalog()
        self.assertEqual(len(catalog), 72)
        self.assertEqual(sum(map(len, catalog.values())), 86)
        self.assertEqual(len(catalog["1072"]), 2)
        for code in ("1145", "1144", "4418", "5164"):
            self.assertIn(code, catalog)

    def test_default_and_bad_codes(self):
        self.assertEqual(select_subject({}), "1145")
        for code in (1144, True, "999999", "1144 ", "", None):
            with self.subTest(code=code), self.assertRaises(ValueError):
                select_subject({"municipal_subject": {"code": code}})

    def test_explicit_choice_and_exact_type(self):
        for code, event in (
            ("1144", "red_white_parking"),
            ("5164", "crosswalk_parking"),
            ("4418", "blocked_ramp"),
            ("1072", "graffiti"),
        ):
            r = record(code, event)
            validate_owner_choice(r, code)
            self.assertEqual(r["violation"]["category"], event)
            for field, value in (
                ("owner_choice", False),
                ("event_type", "sidewalk_parking"),
            ):
                bad = json.loads(json.dumps(r))
                bad["municipal_subject"][field] = value
                with self.assertRaises(ValueError):
                    validate_owner_choice(bad, code)
        r = record()
        del r["approved_description"]
        with self.assertRaises(ValueError):
            validate_owner_choice(r, "1072")

    def test_generic_draft_needs_no_plate_or_inspector(self):
        r = record()
        text = build_report(record_to_car(r))
        self.assertIn("גרפיטי על קיר", text)
        self.assertIn("לטפל במפגע", text)
        self.assertNotIn("מספר רכב", text)
        self.assertNotIn("פקח", text)
        r["violation"]["category"] = ""
        with self.assertRaises(ValueError):
            build_report(record_to_car(r))

    def test_unknown_or_missing_choice_blocks_before_transport(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for choice in (
                None,
                {"code": "1072", "owner_choice": False, "event_type": "graffiti"},
            ):
                r = record()
                r["municipal_subject"] = choice
                (folder / "report.json").write_text(json.dumps(r))
                args = argparse.Namespace(
                    submit=True,
                    approved_sha256="d",
                    folder=folder,
                    state_dir=folder,
                    retry_photo=False,
                )
                with (
                    patch.dict(os.environ, {"PARKING_106_ENABLE_LIVE": "true"}),
                    patch("parking_report.api106.submit") as send,
                ):
                    with self.assertRaises(ValueError):
                        execute(
                            args,
                            {},
                            {"CaseSubjectCode": "1072"},
                            b"",
                            "d",
                            None,
                            {"state": {}},
                            "id",
                        )
                    send.assert_not_called()

    def test_functional_non_vehicle_preview_and_digest(self):
        from PIL import Image

        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            Image.new("RGB", (10, 10), "white").save(folder / "photo.jpg")
            addresses = folder / "addresses.json"
            addresses.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "streets": [
                            {"StreetCode": "fake", "StreetName": "רחוב לדוגמה"}
                        ],
                        "addresses": [[31.77, 35.21, "1", "רחוב לדוגמה"]],
                    }
                )
            )
            r = record()
            env = dict(
                os.environ,
                **{
                    "PARKING_REPORTER_" + k.upper(): v
                    for k, v in {
                        "first_name": "שם",
                        "last_name": "לדוגמה",
                        "phone": "0500000000",
                        "id_number": "000000018",
                        "email": "example@example.invalid",
                    }.items()
                },
            )
            reporter = {
                k: env["PARKING_REPORTER_" + k.upper()]
                for k in ("first_name", "last_name", "phone", "id_number", "email")
            }

            def save():
                (folder / "report.json").write_text(json.dumps(r))

            save()
            _, payload, _, digest = prepare(folder, addresses, reporter)
            self.assertEqual(payload["CaseSubjectCode"], "1072")
            self.assertEqual(payload["CaseDescription"], r["approved_description"])
            cmd = [
                sys.executable,
                "-m",
                "parking_report.api_cli",
                str(folder),
                "--addresses",
                str(addresses),
            ]
            out = subprocess.run(cmd, env=env, capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(json.loads(out.stdout)["network_requests"], 0)
            r["approved_description"] = "  " + r["approved_description"] + "\n"
            save()
            self.assertEqual(
                prepare(folder, addresses, reporter)[1]["CaseDescription"],
                r["approved_description"],
            )
            r = record()
            del r["municipal_subject"]
            r["plate"] = "0000000"
            r["violation"]["category"] = "sidewalk_parking"
            save()
            contact, payload, _, historic_digest = prepare(folder, addresses, reporter)
            from parking_report.api106 import read_picture
            import hashlib

            historic = json.dumps(
                {
                    "reporter": contact,
                    "report": payload,
                    "picture": read_picture(folder / "photo.jpg")[0],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            self.assertEqual(
                historic_digest, hashlib.sha256(historic.encode()).hexdigest()
            )
            for mutate in (
                lambda: r["municipal_subject"].update(code="1016"),
                lambda: r["municipal_subject"].update(owner_choice=False),
                lambda: r["violation"].update(category="broken_sign"),
                lambda: r.update(approved_description="נוסח אחר"),
            ):
                r = record()
                mutate()
                save()
                self.assertNotEqual(prepare(folder, addresses, reporter)[3], digest)
            r["municipal_subject"]["code"] = "999999"
            save()
            out = subprocess.run(cmd, env=env, capture_output=True, text=True)
            self.assertEqual(out.returncode, 4)
            self.assertNotIn("fake", out.stdout)
