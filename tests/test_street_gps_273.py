"""Pin the street-GPS guard from #270/#272: minimum distance and 200 m cutoff (#273).

When an explicit street is given but the requested house has no address point,
``check_street_gps`` requires at least one point of that street within 200 m of
the photo GPS. These tests pin two details the #272 tests left open:

- the guard uses the *nearest* street point (min), so one distant point on an
  otherwise nearby street does not reject it;
- the cutoff is inclusive: exactly 200 m is accepted, anything above is
  rejected.

All coordinates are synthetic. Distances are built on projected (metre)
coordinates, either passed straight to the helper or produced by a fixed
lookup transform patched in for ``api106.ITM``, so no geographic projection
rounding can move a point across the boundary. Offline; never contacts the
municipality.
"""

import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from parking_report import api106
except ImportError:
    api106 = None

STREET = "רחוב ניסוי"  # synthetic street name
STREET_CODE = "9001"
OTHER = "שדרת דוגמה"  # synthetic second street
OTHER_CODE = "9002"
ABSENT_HOUSE = "99"  # no address point has this number

# Synthetic projected origin (inside the usual ITM range for the city). Every
# offset below is an integer or chosen so that the subtraction is exact.
GX, GY = 220000.0, 630000.0
JUST_OVER = math.nextafter(GX + 200.0, math.inf)  # next representable x


def _point(x, y, house="1", code=STREET_CODE, name=STREET):
    """One entry shaped like ``Addresses.points``: (x, y, house, code, name)."""
    return (x, y, house, code, name)


class FixedTransform:
    """Stand-in for ``api106.ITM``: an exact (lon, lat) -> (x, y) lookup."""

    def __init__(self, table):
        self.table = dict(table)

    def transform(self, lon, lat):
        return self.table[(lon, lat)]


@unittest.skipIf(api106 is None, "install the api extra")
class StreetGpsHelperTests(unittest.TestCase):
    """``check_street_gps`` on projected coordinates, no transform involved."""

    def check(self, points, gps_xy=(GX, GY)):
        api106.check_street_gps(points, STREET_CODE, [], gps_xy)

    def assert_rejected(self, points, gps_xy=(GX, GY)):
        with self.assertRaisesRegex(api106.InputError, "street is more than 200 m"):
            self.check(points, gps_xy)

    def test_fixture_distances_are_exact(self):
        # Self-check of the fixtures, so a boundary failure below cannot be a
        # rounding artefact of the test itself.
        self.assertEqual(math.hypot(200.0, 0.0), 200.0)
        self.assertEqual(math.hypot(120.0, 160.0), 200.0)
        self.assertEqual((GX + 200.0) - GX, 200.0)
        over = JUST_OVER - GX
        self.assertGreater(over, 200.0)
        self.assertLess(over, 200.001)

    def test_nearest_point_decides_not_farthest(self):
        near = _point(GX + 10.0, GY, house="2")
        far = _point(GX + 5000.0, GY, house="4")
        for order in ([near, far], [far, near]):
            with self.subTest(first=order[0][2]):
                self.check(order)  # min distance 10 m: accepted

    def test_all_points_distant_rejected(self):
        self.assert_rejected(
            [_point(GX + 5000.0, GY, house="2"), _point(GX + 201.0, GY, house="4")]
        )

    def test_other_street_points_are_ignored(self):
        # A nearby point of a different street must not rescue a distant one.
        self.assert_rejected(
            [
                _point(GX + 1.0, GY, code=OTHER_CODE, name=OTHER),
                _point(GX + 5000.0, GY),
            ]
        )

    def test_exactly_200_m_accepted(self):
        for dx, dy in ((200.0, 0.0), (0.0, -200.0), (-120.0, 160.0)):
            with self.subTest(dx=dx, dy=dy):
                self.check([_point(GX + dx, GY + dy), _point(GX + 900.0, GY)])

    def test_just_over_200_m_rejected(self):
        for dx in (200.001, JUST_OVER - GX):
            with self.subTest(dx=dx):
                self.assert_rejected([_point(GX + dx, GY), _point(GX + 900.0, GY)])

    def test_house_points_present_skip_the_street_guard(self):
        # The house-distance guard owns that case (#270); the helper is a no-op.
        far = [_point(GX + 5000.0, GY)]
        api106.check_street_gps(far, STREET_CODE, far, (GX, GY))


@unittest.skipIf(api106 is None, "install the api extra")
class StreetGpsResolveTests(unittest.TestCase):
    """The same rules through ``Addresses.resolve`` with a fixed transform."""

    # Synthetic (lat, lon) keys inside the supported area; the transform maps
    # each to an exact projected position, so only these offsets matter.
    GPS = (31.8000, 35.2000)

    def resolve(self, offsets, house=ABSENT_HOUSE):
        """Build a dataset with one STREET point per x offset and resolve."""
        table = {(self.GPS[1], self.GPS[0]): (GX, GY)}
        rows = []
        for i, dx in enumerate(offsets):
            lat, lon = 31.8100 + i / 1000, 35.2100
            table[(lon, lat)] = (GX + dx, GY)
            rows.append([lat, lon, str(i + 1), STREET])
        # A point of the other street right at the GPS position.
        table[(35.2050, 31.8050)] = (GX, GY)
        rows.append([31.8050, 35.2050, "1", OTHER])
        data = {
            "version": 1,
            "streets": [
                {"StreetCode": STREET_CODE, "StreetName": STREET},
                {"StreetCode": OTHER_CODE, "StreetName": OTHER},
            ],
            "addresses": rows,
        }
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        with tmp:
            json.dump(data, tmp, ensure_ascii=False)
        self.addCleanup(Path(tmp.name).unlink)
        with patch.object(api106, "ITM", FixedTransform(table)):
            addr = api106.Addresses(Path(tmp.name))
            return addr.resolve(STREET, house, self.GPS)

    def test_multi_point_street_nearby_point_accepts(self):
        for offsets in ((15.0, 4000.0), (4000.0, 15.0)):
            with self.subTest(offsets=offsets):
                result = self.resolve(offsets)
                self.assertEqual(result["street_code"], STREET_CODE)
                self.assertEqual(result["house"], ABSENT_HOUSE)
                self.assertEqual(result["coordinate_source"], "photo_gps")
                self.assertEqual((result["x"], result["y"]), (int(GX), int(GY)))

    def test_multi_point_street_all_distant_rejected(self):
        with self.assertRaisesRegex(api106.InputError, "street is more than 200 m"):
            self.resolve((4000.0, 250.0))

    def test_boundary_through_resolve(self):
        self.assertEqual(self.resolve((200.0, 4000.0))["street_code"], STREET_CODE)
        for dx in (200.001, JUST_OVER - GX):
            with self.subTest(dx=dx):
                with self.assertRaisesRegex(
                    api106.InputError, "street is more than 200 m"
                ):
                    self.resolve((dx, 4000.0))


if __name__ == "__main__":
    unittest.main()
