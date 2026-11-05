"""The fixture refactor must preserve runtime validation and privacy behavior."""

import argparse
import unittest

from synthetic_contact import SYNTHETIC_ID, SYNTHETIC_PHONE

from parking_report.api106 import InputError, make_contact


class SyntheticContactTests(unittest.TestCase):
    def args(self, **changes):
        values = dict(
            national_id=SYNTHETIC_ID,
            phone=SYNTHETIC_PHONE,
            email="synthetic@example.invalid",
            first_name="Synthetic",
            last_name="Fixture",
        )
        values.update(changes)
        return argparse.Namespace(**values)

    def test_existing_synthetic_values_and_valid_contact(self):
        self.assertEqual(SYNTHETIC_PHONE, "05" + "0" * 8)
        self.assertEqual(SYNTHETIC_ID, "0" * 7 + str(18))
        contact = make_contact(self.args())
        self.assertEqual(contact["MobilePhoneNumber"], SYNTHETIC_PHONE)
        self.assertEqual(contact["ContactIdNumber"], SYNTHETIC_ID)

    def test_invalid_checksum_still_rejected(self):
        with self.assertRaises(InputError):
            make_contact(self.args(national_id="0" * 7 + str(19)))

    def test_reserved_international_shape_still_rejected(self):
        with self.assertRaises(InputError):
            make_contact(self.args(phone="+999" + "0" * 8))
