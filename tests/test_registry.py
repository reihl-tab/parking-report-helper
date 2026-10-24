import unittest
from datetime import date

from parking_report.registry import VEHICLES, lookup


class RegistryTests(unittest.TestCase):
    def test_found_and_expiry(self):
        def fake(resource, field, plate):
            row = {field: int(plate)}
            if resource == VEHICLES:
                row.update(tokef_dt="2026-10-08", tozeret_nm="Example")
            else:
                row.update({"SUG TAV": 1, "TAARICH HAFAKAT TAG": 20260101})
            return {"total": 1, "records": [row]}

        out = lookup("00-000-00", date(2026, 10, 8), fake)
        self.assertEqual(out["licence_status"], "valid_on_check_date")
        self.assertEqual(out["disability_status"], "listed_in_dataset")
        self.assertNotIn("misgeret", out["vehicle"])

    def test_absence_remains_unknown(self):
        out = lookup(
            "1234567", date(2026, 10, 8), lambda *a: {"total": 0, "records": []}
        )
        self.assertEqual(out["licence_status"], "unknown")
        self.assertEqual(out["disability_status"], "unknown")

    def test_errors_remain_unknown(self):
        def fake(*args):
            raise OSError()

        out = lookup("1234567", date(2026, 10, 8), fake)
        self.assertEqual(out["vehicle_lookup"], "unverified")

    def test_invalid_plate(self):
        with self.assertRaises(ValueError):
            lookup("abc", date(2026, 10, 8))


from parking_report.registry import describe_he
from parking_report.report import build_report


class DescribeTests(unittest.TestCase):
    base = {"plate": "1234567", "checked_on": "2026-10-08"}

    def matched(self, expiry, tag=None):
        out = dict(
            self.base,
            vehicle_lookup="matched",
            licence_status="valid_on_check_date"
            if expiry >= "2026-10-08"
            else "expired_on_check_date",
            vehicle={
                "tozeret_nm": "סקודה",
                "degem_nm": "אוקטביה",
                "kinuy_mishari": "OCTAVIA",
                "tzeva_rechev": "לבן",
                "tokef_dt": expiry + "T00:00:00",
                "mivchan_acharon_dt": "2026-01-02",
            },
        )
        if tag:
            out.update(
                disability_lookup="matched",
                disability={"SUG TAV": 1, "TAARICH HAFAKAT TAG": 20260101},
            )
        else:
            out["disability_lookup"] = "not_found_in_dataset"
        return out

    def test_valid_with_tag(self):
        text = describe_he(self.matched("2027-03-04", tag=True))
        for part in (
            "סקודה",
            "לבן",
            "עד 4.3.2027 (בתוקף)",
            "תג נכה",
            "קוד סוג תג: 1",
            "2.1.2026",
        ):
            self.assertIn(part, text)

    def test_expired(self):
        self.assertIn("אין לרכב רישיון בתוקף", describe_he(self.matched("2026-09-01")))

    def test_unknown_is_omitted(self):
        text = describe_he(
            dict(
                self.base,
                vehicle_lookup="not_found_in_dataset",
                disability_lookup="unverified",
            )
        )
        self.assertEqual(text, "")
        text = describe_he(
            dict(
                self.base,
                vehicle_lookup="unverified",
                disability_lookup="not_found_in_dataset",
            )
        )
        self.assertNotIn("רישיון", text)
        self.assertNotIn("הושלמה", text)
        self.assertIn("לא נמצא לרכב תג נכה", text)

    def test_no_owner_or_chassis_and_no_result(self):
        self.assertEqual(describe_he(None), "")
        self.assertNotIn("misgeret", describe_he(self.matched("2027-03-04")))

    def test_report_includes_registry_text_once(self):
        car = {
            "plate": "1234567",
            "street": "הפורצים 15",
            "datetime": "2026-10-08 10:00",
            "registry_text": describe_he(self.matched("2027-03-04")),
        }
        text = build_report(car)
        self.assertEqual(text.count("בדיקה במאגרי משרד התחבורה"), 1)
        self.assertNotIn("registry_text", text)
        self.assertNotIn(
            "registry",
            build_report({k: v for k, v in car.items() if k != "registry_text"}),
        )
