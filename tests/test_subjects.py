"""Subject selection, non-parking drafts and offline API end-to-end tests."""

import argparse
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from synthetic_contact import SYNTHETIC_ID, SYNTHETIC_PHONE

from parking_report import api106
from parking_report.api_cli import execute, prepare
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

    def test_recorded_path_picks_the_catalog_row(self):
        # 5164 ("אחר") sits under 12 parents including "חניה אסורה"; the
        # recorded path, not any(), decides whether a plate is required.
        sign = record("5164", "sign_request")
        sign["municipal_subject"]["path"] = ["שילוט ותמרורים", "אחר"]
        validate_owner_choice(sign, "5164")
        car = record_to_car(sign)
        self.assertEqual(car["draft_kind"], "municipal_issue")
        self.assertNotIn("מספר רכב", build_report(car))

        bollard = record("5164", "bollard_request")
        bollard["municipal_subject"]["path"] = ["גדרות, מעקות, עמודים", "אחר"]
        validate_owner_choice(bollard, "5164")
        self.assertEqual(record_to_car(bollard)["draft_kind"], "municipal_issue")

        flooding = record("5164", "bike_path_flooding")
        flooding["municipal_subject"]["path"] = ["נזילות / תקלות מים", "אחר"]
        validate_owner_choice(flooding, "5164")
        self.assertEqual(record_to_car(flooding)["draft_kind"], "municipal_issue")

        parking = record("5164", "crosswalk_parking")
        parking["municipal_subject"]["path"] = ["חניה אסורה", "אחר"]
        validate_owner_choice(parking, "5164")
        with self.assertRaisesRegex(ValueError, "plate"):
            record_to_car(parking)

    def test_missing_or_bad_path_fails_closed(self):
        # No recorded path: any() fallback keeps 5164 parking (plate required).
        with self.assertRaisesRegex(ValueError, "plate"):
            record_to_car(record("5164", "bike_path_flooding"))
        # A real parking subject still requires a plate.
        with self.assertRaisesRegex(ValueError, "plate"):
            record_to_car(record("1145", "sidewalk_parking"))
        for bad_path in (
            "שילוט ותמרורים",
            [],
            [["שילוט ותמרורים", "אחר"]],
            ["שילוט ותמרורים", "נקודה מומצאת"],
            ["נזילות / תקלות מים", "אחר", "אחר"],
        ):
            r = record("5164", "sign_request")
            r["municipal_subject"]["path"] = bad_path
            with self.subTest(path=bad_path):
                with self.assertRaises(ValueError):
                    validate_owner_choice(r, "5164")
                # Intake fails closed too, on the path, not on the plate (#317).
                with self.assertRaisesRegex(ValueError, "path"):
                    record_to_car(r)

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
                        "phone": SYNTHETIC_PHONE,
                        "id_number": SYNTHETIC_ID,
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
            out = subprocess.run(
                cmd, env=env, capture_output=True, text=True, check=False
            )
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
            import hashlib

            from parking_report.api106 import read_picture

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
                lambda rec: rec["municipal_subject"].update(code="1016"),
                lambda rec: rec["municipal_subject"].update(owner_choice=False),
                lambda rec: rec["violation"].update(category="broken_sign"),
                lambda rec: rec.update(approved_description="נוסח אחר"),
            ):
                r = record()
                mutate(r)
                save()
                self.assertNotEqual(prepare(folder, addresses, reporter)[3], digest)
            r["municipal_subject"]["code"] = "999999"
            save()
            out = subprocess.run(
                cmd, env=env, capture_output=True, text=True, check=False
            )
            self.assertEqual(out.returncode, 4)
            self.assertNotIn("fake", out.stdout)


class ApproximateNoteTests(unittest.TestCase):
    """The note wording matches the path the pin actually took (#316 F4b)."""

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.addresses = self.folder / "addresses.json"
        self.addresses.write_text(
            json.dumps(
                {
                    "version": 1,
                    "streets": [{"StreetCode": "9201", "StreetName": "רחוב לדוגמה"}],
                    "addresses": [[31.781000, 35.221000, "1", "רחוב לדוגמה"]],
                }
            ),
            encoding="utf-8",
        )
        self.reporter = {
            "first_name": "שם",
            "last_name": "לדוגמה",
            "phone": SYNTHETIC_PHONE,
            "id_number": SYNTHETIC_ID,
            "email": "example@example.invalid",
        }

    def photo(self, latlon=None):
        from PIL import Image
        from test_read_picture import gps_exif, jpeg_bytes

        raw = jpeg_bytes(
            Image.new("RGB", (10, 10), "white"), gps_exif(*latlon) if latlon else None
        )
        (self.folder / "photo.jpg").write_bytes(raw)

    def record(self, approximate):
        value = record()
        del value["approved_description"]  # build_report path: the note enters the text
        value["address"]["house_number"] = "500"  # absent from the synthetic points
        value["address"]["approximate"] = approximate
        (self.folder / "report.json").write_text(
            json.dumps(value, ensure_ascii=False), encoding="utf-8"
        )

    def test_centroid_pin_says_street_centroid_owner_confirmed(self):
        self.photo()
        self.record(True)
        _, payload, _, _ = prepare(self.folder, self.addresses, self.reporter)
        self.assertEqual(payload["CaseHouseNumber"], "500")
        self.assertIn("מרכז הרחוב", payload["CaseDescription"])
        self.assertIn("אומת מול הבעלים", payload["CaseDescription"])
        self.assertNotIn("נקודת הצילום", payload["CaseDescription"])

    def test_gps_pin_keeps_the_nearest_building_sentence(self):
        self.photo((31.781000, 35.221000))
        self.record(True)
        _, payload, _, _ = prepare(self.folder, self.addresses, self.reporter)
        self.assertIn(
            "הכתובת היא קירוב לפי הבניין הממוספר הקרוב לנקודת הצילום",
            payload["CaseDescription"],
        )

    def test_without_the_flag_the_absent_house_fails_closed(self):
        self.photo()
        self.record(False)
        with self.assertRaisesRegex(api106.InputError, "absent from the address"):
            prepare(self.folder, self.addresses, self.reporter)
