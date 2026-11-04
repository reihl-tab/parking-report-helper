"""Offline contract tests. No live municipality requests or applicant data."""

import dataclasses
import tempfile
import unittest
from pathlib import Path

from parking_report.givatayim import FormError, ResidentReport

SYNTHETIC_FIRST_NAME = "שםבדיקה"
SYNTHETIC_LAST_NAME = "שםמשפחהבדיקה"
SYNTHETIC_MOBILE = "0000000000"
SYNTHETIC_ID = "000000001"  # Deliberately invalid checksum; not a real identity.


def report(**changes):
    values = dict(
        first_name=SYNTHETIC_FIRST_NAME,
        last_name=SYNTHETIC_LAST_NAME,
        mobile_phone=SYNTHETIC_MOBILE,
        national_id=SYNTHETIC_ID,
        resident_street_id="365",
        resident_house_number="1",
        incident_street_id="365",
        incident_house_number="2",
        description="תיאור סינתטי לבדיקה בלבד",
    )
    return ResidentReport(**(values | changes))


class InputTests(unittest.TestCase):
    def test_valid_input(self):
        report().validate()
        self.assertNotIn(SYNTHETIC_ID, repr(report()))

    def test_invalid_fields(self):
        for name, value in [
            ("first_name", ""),
            ("last_name", "a-b"),
            ("mobile_phone", "+0000000000"),
            ("national_id", "1"),
            ("resident_street_id", "javascript:1"),
            ("resident_house_number", ""),
            ("incident_street_id", "-1"),
            ("description", " "),
            ("description", "a" * 701),
            ("attachments", (Path("missing.jpg"),) * 5),
            ("attachments", (Path("missing.jpg"),)),
        ]:
            with self.subTest(name=name), self.assertRaises(FormError) as error:
                report(**{name: value}).validate()
            if str(value):
                self.assertNotIn(str(value), str(error.exception))

    def test_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthetic.png"
            path.write_bytes(b"synthetic")
            report(attachments=(path,)).validate()
            link = Path(tmp) / "link.png"
            link.symlink_to(path)
            with self.assertRaises(FormError):
                report(attachments=(link,)).validate()
            path.write_bytes(b"0" * 10_000_001)
            with self.assertRaises(FormError):
                report(attachments=(path,)).validate()
            path.write_bytes(b"")
            with self.assertRaises(FormError):
                report(attachments=(path,)).validate()

    def test_result_has_no_submit_parameter(self):
        self.assertNotIn("submit", {x.name for x in dataclasses.fields(ResidentReport)})
