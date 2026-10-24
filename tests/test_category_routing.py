import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from parking_report.api_cli import execute


class CategoryRoutingTests(unittest.TestCase):
    def check(self, category, routing, allowed):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "report.json").write_text(
                json.dumps({"violation": {"category": category}})
            )
            args = argparse.Namespace(
                submit=True,
                approved_sha256="digest",
                approved_category_routing=routing,
                folder=folder,
                state_dir=folder / "state",
                retry_photo=False,
            )
            with (
                patch.dict("os.environ", {"PARKING_106_ENABLE_LIVE": "true"}),
                patch("parking_report.api106.Municipality"),
                patch(
                    "parking_report.api106.submit",
                    return_value={"status": "submitted", "photo_attached": True},
                ) as submit,
            ):
                if allowed:
                    execute(
                        args,
                        {},
                        {"CaseSubjectCode": "1145"},
                        b"",
                        "digest",
                        None,
                        {"state": {}},
                        "id",
                    )
                    submit.assert_called_once()
                else:
                    with self.assertRaises(ValueError):
                        execute(
                            args,
                            {},
                            {"CaseSubjectCode": "1145"},
                            b"",
                            "digest",
                            None,
                            {"state": {}},
                            "id",
                        )
                    submit.assert_not_called()

    def test_default_does_not_widen(self):
        for category in ["crosswalk_parking", "blocked_ramp", "other"]:
            self.check(category, "", False)

    def test_exact_type_override_only(self):
        self.check("crosswalk_parking", "crosswalk_parking", True)
        self.check("blocked_ramp", "blocked_ramp", True)
        self.check("crosswalk_parking", "blocked_ramp", False)
        self.check("other", "crosswalk_parking", False)

    def test_sidewalk_default_retained(self):
        self.check("sidewalk_parking", "", True)


class ReviewedDescriptionTests(unittest.TestCase):
    def test_explicit_description_in_payload_and_digest(self):
        import hashlib
        from parking_report.api_cli import prepare
        from parking_report.api106 import Addresses

        with TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "photo.jpg").write_bytes(b"photo")
            record = {
                "schema_version": 1,
                "status": "ready",
                "photo": "photo.jpg",
                "plate": "0000000",
                "captured_at": "2026-01-01 12:00",
                "address": {"street": "רחוב לדוגמה", "house_number": "1"},
                "violation": {"category": "blocked_ramp", "description": "רמפה"},
                "approved_description": "נוסח שנבדק",
            }
            (folder / "report.json").write_text(json.dumps(record))
            reporter = {
                k: "fake"
                for k in ["first_name", "last_name", "phone", "id_number", "email"]
            }
            resolved = {
                "street_code": "fake",
                "street_name": "רחוב לדוגמה",
                "house": "1",
                "x": 1,
                "y": 2,
            }
            with (
                patch(
                    "parking_report.api106.read_picture",
                    return_value=("a" * 64, None, b"photo"),
                ),
                patch("parking_report.api106.make_contact", return_value={}),
                patch.object(Addresses, "__init__", return_value=None),
                patch.object(Addresses, "resolve", return_value=resolved),
            ):
                _, report, _, digest = prepare(
                    folder, folder / "addresses.json", reporter
                )
                self.assertEqual(report["CaseDescription"], "נוסח שנבדק")
                record["approved_description"] = "נוסח אחר"
                (folder / "report.json").write_text(json.dumps(record))
                self.assertNotEqual(
                    prepare(folder, folder / "addresses.json", reporter)[3], digest
                )
