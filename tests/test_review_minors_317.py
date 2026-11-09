"""Review minors left open by #315 and #316 (#317).

- #315 F1: the shared-photo extension checks use the recorded subject path,
  like intake, so a 5164 record under a non-parking parent is not a vehicle.
- #316 F1/F3: prepare() passes only a JSON ``true`` approximate flag on to
  the resolver, and the centroid reaches the municipal payload.
- #316 F2: the centroid averages the chosen street's points only.

Synthetic streets, numbers and points only; nothing leaves the process.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image
from synthetic_contact import SYNTHETIC_ID, SYNTHETIC_PHONE

from parking_report import api106
from parking_report.api_cli import prepare
from parking_report.shared_photo_extension import (
    _check_parent_record,
    extension_manifest,
)

PARKING_PATH = ["חניה אסורה", "אחר"]
WATER_PATH = ["נזילות / תקלות מים", "אחר"]
PLATE_PARENT = "1000001"
PLATE_CHILD = "2000002"
PHOTO = "b" * 64


def subject(path=None, event="crosswalk_parking"):
    value = {"code": "5164", "owner_choice": True, "event_type": event}
    if path is not None:
        value["path"] = path
    return value


def manifest():
    return {
        "parent_id": "parent-a",
        "parent_digest": "a" * 64,
        "photo_sha256": PHOTO,
        "parent_plate": PLATE_PARENT,
        "plate": PLATE_CHILD,
        "authority_reference": "2611-000001",
    }


class ExtensionSubjectPathTests(unittest.TestCase):
    """#315 F1: the same parking answer as intake for multi-parent codes."""

    def child(self, path):
        return {
            "plate": PLATE_CHILD,
            "shared_photo_extension": manifest(),
            "municipal_subject": subject(path),
        }

    def test_extension_with_parking_path_is_a_vehicle(self):
        record = self.child(PARKING_PATH)
        self.assertEqual(extension_manifest(record, "child-b", PHOTO), manifest())

    def test_extension_with_non_parking_path_is_refused(self):
        with self.assertRaisesRegex(ValueError, "reports a vehicle"):
            extension_manifest(self.child(WATER_PATH), "child-b", PHOTO)

    def test_extension_with_bad_path_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "path"):
            extension_manifest(self.child(["חניה אסורה"]), "child-b", PHOTO)

    def test_parent_with_parking_path_is_eligible(self):
        parent = {"plate": PLATE_PARENT, "municipal_subject": subject(PARKING_PATH)}
        _check_parent_record(parent, manifest())  # no error

    def test_parent_with_non_parking_path_is_ineligible(self):
        parent = {"plate": PLATE_PARENT, "municipal_subject": subject(WATER_PATH)}
        with self.assertRaisesRegex(ValueError, "not a vehicle intake"):
            _check_parent_record(parent, manifest())

    def test_parent_with_bad_path_is_invalid(self):
        for path in (["חניה אסורה"], [], "חניה אסורה"):
            parent = {"plate": PLATE_PARENT, "municipal_subject": subject(path)}
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "parent subject is invalid"):
                    _check_parent_record(parent, manifest())


STREETS = [
    {"StreetCode": "9101", "StreetName": "רחוב הדוגמה"},
    {"StreetCode": "9102", "StreetName": "סמטת הבדיקה"},
]
# Two points on the chosen street, one on another street far to the east.
POINTS = [
    [31.7800, 35.2200, "1", "רחוב הדוגמה"],
    [31.7810, 35.2210, "3", "רחוב הדוגמה"],
    [31.7800, 35.2600, "5", "סמטת הבדיקה"],
]


def centroid(*latlons):
    xys = [api106.ITM.transform(lon, lat) for (lat, lon) in latlons]
    return (
        int(sum(x for x, _ in xys) / len(xys)),
        int(sum(y for _, y in xys) / len(xys)),
    )


class ApproximateCentroidTests(unittest.TestCase):
    """#316 F1, F2 and F3."""

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        # A photo without EXIF: no GPS, so the house lookup decides.
        Image.new("RGB", (10, 10), "white").save(self.folder / "photo.jpg")
        self.addresses = self.folder / "addresses.json"
        self.addresses.write_text(
            json.dumps({"version": 1, "streets": STREETS, "addresses": POINTS}),
            encoding="utf-8",
        )
        self.reporter = {
            "first_name": "שם",
            "last_name": "לדוגמה",
            "phone": SYNTHETIC_PHONE,
            "id_number": SYNTHETIC_ID,
            "email": "example@example.invalid",
        }

    def prepare_with(self, **address):
        record = {
            "schema_version": 1,
            "status": "ready",
            "photo": "photo.jpg",
            "captured_at": "2026-01-01 12:00",
            "address": {"street": "רחוב הדוגמה", "house_number": "77"} | address,
            "violation": {"category": "graffiti", "description": "גרפיטי על קיר"},
            "municipal_subject": {
                "code": "1072",
                "owner_choice": True,
                "event_type": "graffiti",
            },
            "approved_description": "גרפיטי על קיר ברחוב הדוגמה 77. אבקש לטפל במפגע.",
        }
        (self.folder / "report.json").write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8"
        )
        return prepare(self.folder, self.addresses, self.reporter)[1]

    def test_approximate_true_reaches_payload_as_street_centroid(self):
        payload = self.prepare_with(approximate=True)
        self.assertEqual(payload["CaseStreetCode"], "9101")
        self.assertEqual(payload["CaseHouseNumber"], "77")
        self.assertEqual(payload["CaseAddressText"], "רחוב הדוגמה 77")
        # Only the chosen street's points: the other street would pull east.
        self.assertEqual(
            (payload["CoordinateX"], payload["CoordinateY"]),
            centroid((31.7800, 35.2200), (31.7810, 35.2210)),
        )

    def test_without_flag_absent_house_fails_closed(self):
        with self.assertRaisesRegex(api106.InputError, "absent from the address"):
            self.prepare_with()

    def test_only_json_true_opts_in(self):
        for value in (False, "false", "true", 1, 0, "0", None, [], {}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    api106.InputError, "absent from the address"
                ):
                    self.prepare_with(approximate=value)


if __name__ == "__main__":
    unittest.main()
