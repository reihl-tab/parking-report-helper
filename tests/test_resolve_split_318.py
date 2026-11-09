"""Characterization tests for Addresses.resolve before and after #318.

#318 splits resolve into module-level helpers. These tests pin the paths
that were not yet pinned by message or result, and pass on the code before
the split as well as after it. Synthetic street names and points only.
"""

import json
import tempfile
import unittest
from pathlib import Path

from parking_report import api106

STREETS = [
    {"StreetCode": "9001", "StreetName": "רחוב הבדיקה"},
    {"StreetCode": "9002", "StreetName": "שדרות הדוגמה"},
]
# About 31.78 N / 35.22 E: inside the Jerusalem box, but all names are made up.
POINTS = [
    [31.78000, 35.22000, "3", "רחוב הבדיקה"],
    [31.78090, 35.22000, "5", "רחוב הבדיקה"],  # about 100 m north of 3
    [31.79000, 35.23000, "8", "רחוב הבדיקה"],
    [31.79150, 35.23000, "8", "רחוב הבדיקה"],  # same number, about 165 m away
    [31.78400, 35.22000, "1", "שדרות הדוגמה"],
    [31.78403, 35.22000, "2", "שדרות הדוגמה"],  # 3 m from 1: ambiguous by GPS
    [31.77000, 35.21000, "4", "שם לא ידוע"],  # name with no street code
]


def xy(lat, lon):
    return api106.ITM.transform(lon, lat)


class ResolveSplitCharacterization(unittest.TestCase):
    def setUp(self):
        data = {"version": 1, "streets": STREETS, "addresses": POINTS}
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        with tmp:
            json.dump(data, tmp, ensure_ascii=False)
        self.addCleanup(Path(tmp.name).unlink)
        self.addr = api106.Addresses(Path(tmp.name))

    def test_named_address_lookup_without_gps(self):
        result = self.addr.resolve("רחוב הבדיקה", "3", None)
        (x, y) = xy(31.78000, 35.22000)
        self.assertEqual(
            result,
            {
                "street_code": "9001",
                "street_name": "רחוב הבדיקה",
                "house": "3",
                "x": int(x),
                "y": int(y),
                "coordinate_source": "address_lookup",
            },
        )

    def test_named_address_with_gps_keeps_photo_coordinates(self):
        gps = (31.78010, 35.22000)
        result = self.addr.resolve("רחוב הבדיקה", "3", gps)
        (x, y) = xy(*gps)
        self.assertEqual(result["coordinate_source"], "photo_gps")
        self.assertEqual((result["x"], result["y"]), (int(x), int(y)))
        self.assertEqual(result["house"], "3")

    def test_unknown_street_checked_before_house_number(self):
        with self.assertRaisesRegex(api106.InputError, "^Street name has no unique"):
            self.addr.resolve("רחוב שאינו קיים", "not a number", None)

    def test_invalid_house_number(self):
        for house in ("abc", "", "1" * 17):
            with self.subTest(house=house):
                with self.assertRaisesRegex(
                    api106.InputError, "^Invalid house number.$"
                ):
                    self.addr.resolve("רחוב הבדיקה", house, None)

    def test_house_far_from_gps(self):
        with self.assertRaisesRegex(
            api106.InputError, "^The supplied address is more than 200 m"
        ):
            self.addr.resolve("רחוב הבדיקה", "3", (31.78300, 35.22000))

    def test_house_with_spread_out_points(self):
        with self.assertRaisesRegex(
            api106.InputError, "^This address has multiple locations."
        ):
            self.addr.resolve("רחוב הבדיקה", "8", None)

    def test_gps_only_picks_one_clear_address(self):
        gps = (31.78002, 35.22000)
        result = self.addr.resolve("", "ignored", gps)
        (x, y) = xy(*gps)
        self.assertEqual(
            result,
            {
                "street_code": "9001",
                "street_name": "רחוב הבדיקה",
                "house": "3",
                "x": int(x),
                "y": int(y),
                "coordinate_source": "photo_gps",
            },
        )

    def test_gps_only_needs_gps(self):
        with self.assertRaisesRegex(
            api106.InputError, "^Photo has no usable GPS. Supply --address"
        ):
            self.addr.resolve(None, "3", None)

    def test_gps_only_ambiguous_lists_nearby(self):
        with self.assertRaisesRegex(
            api106.InputError,
            "Nearby: שדרות הדוגמה 1 \\(\\d+ m\\); שדרות הדוגמה 2 \\(\\d+ m\\)$",
        ):
            self.addr.resolve(None, "", (31.78401, 35.22000))

    def test_gps_only_nothing_nearby(self):
        with self.assertRaisesRegex(api106.InputError, "Nearby: none$"):
            self.addr.resolve(None, "", (31.90000, 35.30000))

    def test_gps_only_nearest_without_street_code(self):
        with self.assertRaisesRegex(api106.InputError, "Nearby: שם לא ידוע 4 \\("):
            self.addr.resolve(None, "", (31.77001, 35.21000))

    def test_gps_only_too_far_from_nearest(self):
        # About 50 m from house 3: within 150 m but beyond the 35 m match.
        with self.assertRaisesRegex(api106.InputError, "Nearby: רחוב הבדיקה 3 \\("):
            self.addr.resolve(None, "", (31.77955, 35.22000))


if __name__ == "__main__":
    unittest.main()
