"""Direct tests for the record_to_car helpers split out in #66 PR 2.

record_to_car is covered end to end by tests/test_characterize_66.py; these
tests pin each helper's own contract.
"""

import unittest

from parking_report.intake import _build_car, _validate_record, record_to_car

REGISTRY_SAMPLE = {
    "plate": "1234567",
    "checked_on": "2026-10-27",
    "vehicle_lookup": "not_found_in_dataset",
    "disability_lookup": "matched",
    "disability": {
        "SUG TAV": "A",
        "TAARICH HAFAKAT TAG": "2025-01-15",
    },
}
REGISTRY_TEXT = (
    "בדיקה במאגרי משרד התחבורה (נכון ל-27.10.2026): לרכב רשום תג נכה במאגר "
    "תגי הנכים (קוד סוג תג: A), הונפק ב-15.1.2025."
)
APPROX_NOTE = ". הכתובת היא קירוב לפי הבניין הממוספר הקרוב לנקודת הצילום"


def intake_record(**changes):
    """A minimal valid intake record (same shape as test_characterize_66)."""
    return {
        "schema_version": 1,
        "status": "ready",
        "plate": "00-000-00",
        "captured_at": "2026-10-27 14:30",
        "car_type": "פרטי",
        "address": {"street": "רחוב הדוגמה", "house_number": "42"},
        "violation": {"description": "חונה על המדרכה"},
    } | changes


class TestValidateRecord(unittest.TestCase):
    def test_returns_record_parts(self):
        record = intake_record(
            address={"street": " רחוב הדוגמה ", "house_number": " 42 ", "x": 1}
        )
        self.assertEqual(
            _validate_record(record),
            (
                record,
                {"description": "חונה על המדרכה"},
                {"street": " רחוב הדוגמה ", "house_number": " 42 ", "x": 1},
                "רחוב הדוגמה",
                "42",
            ),
        )

    def test_returns_the_same_record_object(self):
        record = intake_record()
        self.assertIs(_validate_record(record)[0], record)

    def test_non_dict_violation_becomes_empty(self):
        _, violation, _, _, _ = _validate_record(intake_record(violation="x"))
        self.assertEqual(violation, {})

    def test_non_dict_address_reports_missing_street(self):
        with self.assertRaisesRegex(ValueError, "^address.street is missing$"):
            _validate_record(intake_record(address=["רחוב", "1"]))

    def test_status_question_is_included(self):
        with self.assertRaisesRegex(ValueError, "^status is 'draft': איזה רחוב\\?$"):
            _validate_record(
                intake_record(status="draft", violation={"question": "איזה רחוב?"})
            )

    def test_missing_status(self):
        record = intake_record()
        del record["status"]
        with self.assertRaisesRegex(ValueError, "^status is 'missing'$"):
            _validate_record(record)

    def test_check_order_status_before_address(self):
        with self.assertRaisesRegex(ValueError, "^status is 'draft'$"):
            _validate_record(intake_record(status="draft", address={}))

    def test_missing_house_number(self):
        with self.assertRaisesRegex(ValueError, "^address.house_number is missing; "):
            _validate_record(intake_record(address={"street": "רחוב"}))


class TestBuildCar(unittest.TestCase):
    def test_parking_full_mapping(self):
        record = intake_record(
            location_description="ליד עץ",
            registry=REGISTRY_SAMPLE,
        )
        car = _build_car(
            record,
            record["violation"],
            {"approximate": True},
            "רחוב הדוגמה 42",
            True,
        )
        self.assertEqual(
            car,
            {
                "plate": "00-000-00",
                "street": "רחוב הדוגמה 42",
                "datetime": "2026-10-27 14:30",
                "car_type": "פרטי",
                "notes": "חונה על המדרכה. תיאור המקום: ליד עץ" + APPROX_NOTE,
                "registry_text": REGISTRY_TEXT,
            },
        )

    def test_municipal_issue_mapping(self):
        record = intake_record(plate="", registry=REGISTRY_SAMPLE)
        car = _build_car(
            record,
            {"description": "סדק", "category": "road_damage"},
            {},
            "רחוב 1",
            False,
        )
        self.assertEqual(
            car,
            {
                "plate": "",
                "street": "רחוב 1",
                "datetime": "2026-10-27 14:30",
                "car_type": "פרטי",
                "notes": "סדק",
                "draft_kind": "municipal_issue",
                "event_type": "road_damage",
            },
        )

    def test_does_not_check_required_fields(self):
        record = intake_record(plate="", captured_at="")
        car = _build_car(record, {}, {}, "רחוב 1", True)
        self.assertEqual((car["plate"], car["datetime"], car["notes"]), ("", "", ""))


class TestRecordToCarComposition(unittest.TestCase):
    def test_street_line_is_street_and_number(self):
        car = record_to_car(
            intake_record(address={"street": "רחוב", "house_number": "7א"})
        )
        self.assertEqual(car["street"], "רחוב 7א")

    def test_required_fields_checked_after_build(self):
        with self.assertRaisesRegex(ValueError, "^plate is missing$"):
            record_to_car(intake_record(plate=""))
        with self.assertRaisesRegex(ValueError, "^datetime is missing$"):
            record_to_car(intake_record(captured_at=""))

    def test_description_check_comes_before_required_fields(self):
        with self.assertRaisesRegex(
            ValueError, "^A municipal issue needs an observed description$"
        ):
            record_to_car(
                intake_record(
                    captured_at="",
                    violation={},
                    municipal_subject={"code": "1015"},
                )
            )


if __name__ == "__main__":
    unittest.main()
