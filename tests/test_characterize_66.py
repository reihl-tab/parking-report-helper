"""Characterization tests for status_draft and record_to_car (#66 PR 0).

These tests pin the CURRENT behavior before refactoring. They must pass on
unchanged src/ and assert exact output.
"""

import unittest

from parking_report.intake import record_to_car
from parking_report.status import status_draft


def status_row(**changes):
    """Create a minimal valid row for status_draft with optional field overrides."""
    return {
        "folder_id": "char-001",
        "filed_date": "2026-10-20",
        "street": "רחוב הבדיקה",
        "event_type": "sidewalk",
        "municipal_ref": "2610-999001",
        "ref_mapping": "verified_receipt",
        "receipt_evidence": "test-receipt.json",
        "plate": "12-345-67",
        "x_post_url": "",
        "facebook_post_url": "",
        "municipal_response": "no_reply",
        "response_checked_at": "2026-10-27",
        "response_evidence": "test-mail",
        "last_reminder_at": "",
    } | changes


def intake_record(**changes):
    """Create a minimal valid intake record for record_to_car with optional field overrides."""
    return {
        "schema_version": 1,
        "status": "ready",
        "plate": "00-000-00",
        "captured_at": "2026-10-27 14:30",
        "car_type": "פרטי",
        "address": {
            "street": "רחוב הדוגמה",
            "house_number": "42",
        },
        "violation": {
            "description": "חונה על המדרכה",
        },
    } | changes


class TestStatusValidationBoundaryConditions(unittest.TestCase):
    """Test each validation path in status_draft in isolation."""

    def test_invalid_date_format(self):
        """Pin 'Invalid date' validation error message."""
        with self.assertRaisesRegex(ValueError, "^Invalid date$"):
            status_draft([status_row(filed_date="not-a-date")], "2026-10-27")

    def test_missing_ledger_columns(self):
        """Pin 'Missing ledger columns' validation error message."""
        incomplete = status_row()
        del incomplete["folder_id"]
        with self.assertRaisesRegex(ValueError, "^Missing ledger columns$"):
            status_draft([incomplete], "2026-10-27")

    def test_unsafe_ledger_value(self):
        """Pin 'Unsafe ledger value' validation error message."""
        with self.assertRaisesRegex(ValueError, "^Unsafe ledger value$"):
            status_draft([status_row(folder_id=123)], "2026-10-27")

    def test_missing_report_identity(self):
        """Pin 'Missing report identity' validation error message."""
        with self.assertRaisesRegex(ValueError, "^Missing report identity$"):
            status_draft([status_row(folder_id="")], "2026-10-27")

    def test_future_filing_date(self):
        with self.assertRaisesRegex(ValueError, "^Future filing date$"):
            status_draft([status_row(filed_date="2026-10-28")], "2026-10-27")

    def test_duplicate_folder_ids(self):
        rows = [
            status_row(folder_id="dup-001", municipal_ref="2610-001"),
            status_row(folder_id="dup-001", municipal_ref="2610-002"),
        ]
        with self.assertRaisesRegex(ValueError, "^Duplicate report ID$"):
            status_draft(rows, "2026-10-27")

    def test_invalid_municipal_ref_format(self):
        with self.assertRaisesRegex(ValueError, "^Invalid or duplicate reference$"):
            status_draft([status_row(municipal_ref="INVALID-REF")], "2026-10-27")

    def test_duplicate_municipal_refs(self):
        rows = [
            status_row(folder_id="test-001", municipal_ref="2610-999"),
            status_row(folder_id="test-002", municipal_ref="2610-999"),
        ]
        with self.assertRaisesRegex(ValueError, "^Invalid or duplicate reference$"):
            status_draft(rows, "2026-10-27")

    def test_unknown_response_status(self):
        with self.assertRaisesRegex(ValueError, "^Unknown response status$"):
            status_draft(
                [status_row(municipal_response="invalid_status")], "2026-10-27"
            )

    def test_response_state_requires_evidence(self):
        # no_reply requires both checked date and evidence
        with self.assertRaisesRegex(
            ValueError, "^Response state requires checked date and source evidence$"
        ):
            status_draft(
                [status_row(municipal_response="no_reply", response_checked_at="")],
                "2026-10-27",
            )

        with self.assertRaisesRegex(
            ValueError, "^Response state requires checked date and source evidence$"
        ):
            status_draft(
                [status_row(municipal_response="no_reply", response_evidence="")],
                "2026-10-27",
            )

    def test_invalid_response_check_date_range(self):
        # checked date must be between filed and today
        with self.assertRaisesRegex(ValueError, "^Invalid response check date$"):
            status_draft(
                [status_row(filed_date="2026-10-20", response_checked_at="2026-10-19")],
                "2026-10-27",
            )

        with self.assertRaisesRegex(ValueError, "^Invalid response check date$"):
            status_draft([status_row(response_checked_at="2026-10-28")], "2026-10-27")

    def test_invalid_reminder_date_range(self):
        # reminder date must be between filed and today
        with self.assertRaisesRegex(ValueError, "^Invalid reminder date$"):
            status_draft(
                [status_row(filed_date="2026-10-20", last_reminder_at="2026-10-19")],
                "2026-10-27",
            )

        with self.assertRaisesRegex(ValueError, "^Invalid reminder date$"):
            status_draft([status_row(last_reminder_at="2026-10-28")], "2026-10-27")


def _data_rows(output: str) -> list[str]:
    """Return the Markdown table's data rows (header and separator excluded)."""
    rows = [line for line in output.splitlines() if line.startswith("| ")]
    return rows[2:]


class TestStatusClassificationDecisionTable(unittest.TestCase):
    """Verify each of the 7 status classification branches with minimal inputs."""

    def test_branch_received(self):
        """response == 'received' → 'מענה מתועד', not due"""
        output = status_draft([status_row(municipal_response="received")], "2026-10-27")
        self.assertEqual(
            _data_rows(output),
            [
                "| char-001 | 2026-10-20 | רחוב הבדיקה | 12-345-67 | 2610-999001 | מאומת ביומן | חסר | חסר | מענה מתועד | מענה מתועד |",
            ],
        )
        self.assertIn("\n## טיוטות תזכורת: 0\n", output)

    def test_branch_unknown(self):
        """response == 'unknown' → 'מענה לא נבדק', not due"""
        output = status_draft(
            [
                status_row(
                    municipal_response="",
                    response_checked_at="",
                    response_evidence="",
                )
            ],
            "2026-10-27",
        )
        self.assertEqual(
            _data_rows(output),
            [
                "| char-001 | 2026-10-20 | רחוב הבדיקה | 12-345-67 | 2610-999001 | מאומת ביומן | חסר | חסר | לא ידוע | מענה לא נבדק |",
            ],
        )
        self.assertIn("\n## טיוטות תזכורת: 0\n", output)

    def test_branch_checked_not_today(self):
        """checked != today → 'נדרשת בדיקת מענה עדכנית', not due"""
        output = status_draft(
            [status_row(response_checked_at="2026-10-26")], "2026-10-27"
        )
        self.assertEqual(
            _data_rows(output),
            [
                "| char-001 | 2026-10-20 | רחוב הבדיקה | 12-345-67 | 2610-999001 | מאומת ביומן | חסר | חסר | ללא מענה לפי בדיקה | נדרשת בדיקת מענה עדכנית |",
            ],
        )
        self.assertIn("\n## טיוטות תזכורת: 0\n", output)

    def test_branch_age_under_seven_days(self):
        """age < 7 → 'טרם חלף שבוע', not due"""
        output = status_draft([status_row(filed_date="2026-10-22")], "2026-10-27")
        self.assertEqual(
            _data_rows(output),
            [
                "| char-001 | 2026-10-22 | רחוב הבדיקה | 12-345-67 | 2610-999001 | מאומת ביומן | חסר | חסר | ללא מענה לפי בדיקה | טרם חלף שבוע |",
            ],
        )
        self.assertIn("\n## טיוטות תזכורת: 0\n", output)

    def test_branch_age_boundary_six_days(self):
        """Pin 6-day boundary: age < REMINDER_DAYS → not due"""
        output = status_draft([status_row(filed_date="2026-10-21")], "2026-10-27")
        self.assertEqual(
            _data_rows(output),
            [
                "| char-001 | 2026-10-21 | רחוב הבדיקה | 12-345-67 | 2610-999001 | מאומת ביומן | חסר | חסר | ללא מענה לפי בדיקה | טרם חלף שבוע |",
            ],
        )
        self.assertIn("\n## טיוטות תזכורת: 0\n", output)

    def test_branch_age_boundary_seven_days(self):
        """Pin 7-day boundary: age >= REMINDER_DAYS → due"""
        output = status_draft([status_row(filed_date="2026-10-20")], "2026-10-27")
        self.assertEqual(
            _data_rows(output),
            [
                "| char-001 | 2026-10-20 | רחוב הבדיקה | 12-345-67 | 2610-999001 | מאומת ביומן | חסר | חסר | ללא מענה לפי בדיקה | מועמד לתזכורת; נדרש אישור |",
            ],
        )
        self.assertIn("\n## טיוטות תזכורת: 1\n", output)

    def test_branch_reminded_already(self):
        """reminded >= checked → 'כבר נשלחה תזכורת', not due"""
        output = status_draft(
            [
                status_row(
                    filed_date="2026-10-15",
                    response_checked_at="2026-10-27",
                    last_reminder_at="2026-10-27",
                )
            ],
            "2026-10-27",
        )
        self.assertEqual(
            _data_rows(output),
            [
                "| char-001 | 2026-10-15 | רחוב הבדיקה | 12-345-67 | 2610-999001 | מאומת ביומן | חסר | חסר | ללא מענה לפי בדיקה | כבר נשלחה תזכורת לפי הבדיקה הנוכחית |",
            ],
        )
        self.assertIn("\n## טיוטות תזכורת: 0\n", output)

    def test_branch_mapping_unverified(self):
        """not mapping → 'שיוך הפנייה דורש אימות', not due"""
        output = status_draft(
            [status_row(filed_date="2026-10-15", ref_mapping="unverified_fifo")],
            "2026-10-27",
        )
        self.assertEqual(
            _data_rows(output),
            [
                "| char-001 | 2026-10-15 | רחוב הבדיקה | 12-345-67 | 2610-999001 | לא מאומת | חסר | חסר | ללא מענה לפי בדיקה | חלף שבוע; שיוך הפנייה דורש אימות |",
            ],
        )
        self.assertIn("\n## טיוטות תזכורת: 0\n", output)

    def test_branch_due_for_reminder(self):
        """All conditions met → 'מועמד לתזכורת', IS due"""
        output = status_draft(
            [
                status_row(
                    filed_date="2026-10-15",
                    response_checked_at="2026-10-27",
                    municipal_response="no_reply",
                )
            ],
            "2026-10-27",
        )
        self.assertEqual(
            _data_rows(output),
            [
                "| char-001 | 2026-10-15 | רחוב הבדיקה | 12-345-67 | 2610-999001 | מאומת ביומן | חסר | חסר | ללא מענה לפי בדיקה | מועמד לתזכורת; נדרש אישור |",
            ],
        )
        self.assertIn("\n## טיוטות תזכורת: 1\n", output)
        self.assertIn("### פנייה 2610-999001", output)


class TestStatusOutputFormatStability(unittest.TestCase):
    """Snapshot test for exact Markdown structure (table header, pipes, reminder format)."""

    def test_exact_markdown_structure_single_row(self):
        """Pin the exact output format for a single row that is due for reminder."""
        rows = [
            {
                "folder_id": "snap-001",
                "filed_date": "2026-10-15",
                "street": "רחוב הצילום",
                "event_type": "sidewalk",
                "plate": "11-222-33",
                "municipal_ref": "2610-888001",
                "ref_mapping": "verified_receipt",
                "receipt_evidence": "snapshot-receipt.json",
                "x_post_url": "https://x.test/post1",
                "facebook_post_url": "https://fb.test/post1",
                "municipal_response": "no_reply",
                "response_checked_at": "2026-10-27",
                "response_evidence": "snap-mail",
                "last_reminder_at": "",
            }
        ]

        output = status_draft(rows, "2026-10-27")

        # These assertIn checks are kept on purpose as readable failure messages;
        # the assertEqual on the full output below is the actual pin.
        # Pin exact header
        self.assertIn("# טיוטת מצב פרטית ליום 2026-10-27", output)

        # Pin exact table header
        expected_header = (
            "| מזהה דיווח | תאריך | רחוב | לוחית (פרטי) | מספר פנייה | שיוך | "
            "X | Facebook | מענה | מצב |"
        )
        self.assertIn(expected_header, output)

        # Pin exact separator
        self.assertIn(
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |", output
        )

        # Pin exact row format
        expected_row = (
            "| snap-001 | 2026-10-15 | רחוב הצילום | 11-222-33 | 2610-888001 | "
            "מאומת ביומן | https://x.test/post1 | https://fb.test/post1 | "
            "ללא מענה לפי בדיקה | מועמד לתזכורת; נדרש אישור |"
        )
        self.assertIn(expected_row, output)

        # Pin exact reminder section format
        self.assertIn("## טיוטות תזכורת: 1", output)
        self.assertIn("### פנייה 2610-888001", output)
        self.assertIn(
            "שלום, אבקש עדכון על פנייה 2610-888001 שנפתחה בתאריך 2026-10-15. תודה.",
            output,
        )

        # Pin the whole output byte for byte (the refactor must not change it)
        expected_full = (
            "# טיוטת מצב פרטית ליום 2026-10-27\n"
            "\n"
            "לבדיקה בלבד. אין שליחה אוטומטית. נמענים ונוסח סופי דורשים אישור.\n"
            "מועמד לתזכורת רק אחרי שבעה ימים, בדיקת מענה עדכנית ושיוך פנייה מאומת ביומן.\n"
            "היעדר שדה מענה אינו הוכחה שלא התקבל מענה. אישור פתיחת פנייה אינו מענה לטיפול.\n"
            "\n"
            "| מזהה דיווח | תאריך | רחוב | לוחית (פרטי) | מספר פנייה | שיוך | X | Facebook | מענה | מצב |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| snap-001 | 2026-10-15 | רחוב הצילום | 11-222-33 | 2610-888001 | מאומת ביומן | https://x.test/post1 | https://fb.test/post1 | ללא מענה לפי בדיקה | מועמד לתזכורת; נדרש אישור |\n"
            "\n"
            "## טיוטות תזכורת: 1\n"
            "\n"
            "יש לבדוק שוב את המענה ואת מקור שיוך הפנייה לפני שליחה; הקוד קורא סימונים, לא מאמת מקורות.\n"
            "\n"
            "### פנייה 2610-888001\n"
            "\n"
            "שלום, אבקש עדכון על פנייה 2610-888001 שנפתחה בתאריך 2026-10-15. תודה.\n"
        )
        self.assertEqual(output, expected_full)

    def test_full_output_snapshot_with_empty_optionals(self):
        """Pin received/unknown labels, 'חסר' placeholder, and 'לא מאומת' with empty fields."""
        rows = [
            status_row(
                filed_date="2026-10-25",
                folder_id="empty-001",
                municipal_ref="2610-777001",
                plate="",
                ref_mapping="unverified_fifo",
                municipal_response="",
                response_checked_at="",
                response_evidence="",
            ),
            status_row(
                filed_date="2026-10-24",
                folder_id="rcvd-002",
                municipal_ref="2610-777002",
                plate="11-111-11",
                municipal_response="received",
            ),
        ]

        output = status_draft(rows, "2026-10-27")
        expected = "# טיוטת מצב פרטית ליום 2026-10-27\n\nלבדיקה בלבד. אין שליחה אוטומטית. נמענים ונוסח סופי דורשים אישור.\nמועמד לתזכורת רק אחרי שבעה ימים, בדיקת מענה עדכנית ושיוך פנייה מאומת ביומן.\nהיעדר שדה מענה אינו הוכחה שלא התקבל מענה. אישור פתיחת פנייה אינו מענה לטיפול.\n\n| מזהה דיווח | תאריך | רחוב | לוחית (פרטי) | מספר פנייה | שיוך | X | Facebook | מענה | מצב |\n| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n| rcvd-002 | 2026-10-24 | רחוב הבדיקה | 11-111-11 | 2610-777002 | מאומת ביומן | חסר | חסר | מענה מתועד | מענה מתועד |\n| empty-001 | 2026-10-25 | רחוב הבדיקה | חסר | 2610-777001 | לא מאומת | חסר | חסר | לא ידוע | מענה לא נבדק |\n\n## טיוטות תזכורת: 0\n\nיש לבדוק שוב את המענה ואת מקור שיוך הפנייה לפני שליחה; הקוד קורא סימונים, לא מאמת מקורות.\n"
        self.assertEqual(output, expected)

    def test_row_ordering_by_filed_date_then_folder_id(self):
        """Pin that rows are sorted by (filed_date, folder_id)."""
        rows = [
            status_row(
                folder_id="z-later", filed_date="2026-10-20", municipal_ref="2610-003"
            ),
            status_row(
                folder_id="a-earlier", filed_date="2026-10-20", municipal_ref="2610-001"
            ),
            status_row(
                folder_id="m-middle", filed_date="2026-10-15", municipal_ref="2610-002"
            ),
        ]

        output = status_draft(rows, "2026-10-27")
        # Data rows in order: earliest filed_date first, then folder_id.
        folder_ids = [
            row.split(" | ")[0].removeprefix("| ") for row in _data_rows(output)
        ]
        self.assertEqual(folder_ids, ["m-middle", "a-earlier", "z-later"])


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


class TestRecordToCarFieldMapping(unittest.TestCase):
    """Verify each intake field maps to correct Car field."""

    def test_basic_field_mapping(self):
        """Pin the exact mapping of basic fields."""
        record = intake_record(
            plate="55-666-77",
            captured_at="2026-10-27 16:45",
            car_type="מסחרי",
            address={"street": "שדרות הבדיקה", "house_number": "123"},
            violation={"description": "חוסם את הכניסה"},
        )

        car = record_to_car(record)

        # Whole-dict equality pins the exact key set as well as the values.
        self.assertEqual(
            car,
            {
                "plate": "55-666-77",
                "street": "שדרות הבדיקה 123",
                "datetime": "2026-10-27 16:45",
                "car_type": "מסחרי",
                "notes": "חוסם את הכניסה",
            },
        )

    def test_location_description_appended_to_notes(self):
        """Pin that location_description is appended to notes with prefix."""
        record = intake_record(
            violation={"description": "על המדרכה"},
            location_description="ליד עץ גדול",
        )

        car = record_to_car(record)

        self.assertEqual(car["notes"], "על המדרכה. תיאור המקום: ליד עץ גדול")

    def test_approximate_address_appended_to_notes(self):
        """Pin that approximate address adds specific note."""
        record = intake_record(
            address={
                "street": "רחוב מקורב",
                "house_number": "99",
                "approximate": True,
            }
        )

        car = record_to_car(record)

        self.assertEqual(
            car["notes"],
            "חונה על המדרכה. הכתובת היא קירוב לפי הבניין הממוספר הקרוב לנקודת הצילום",
        )

    def test_registry_text_added_for_parking_reports(self):
        """Pin that registry info is added as registry_text for parking reports."""
        record = intake_record(
            plate="1234567",
            registry=REGISTRY_SAMPLE,
        )

        car = record_to_car(record)

        self.assertEqual(
            car["registry_text"],
            "בדיקה במאגרי משרד התחבורה (נכון ל-27.10.2026): לרכב רשום תג נכה במאגר תגי הנכים (קוד סוג תג: A), הונפק ב-15.1.2025.",
        )

    def test_municipal_issue_sets_draft_kind_and_event_type(self):
        """Pin draft_kind='municipal_issue' and event_type from category for non-parking."""
        record = intake_record(
            plate="",
            violation={
                "description": "סדק בכביש",
                "category": "road_damage",
            },
            municipal_subject={"code": "1015"},
            registry=REGISTRY_SAMPLE,
        )

        car = record_to_car(record)

        # Unconditional asserts
        self.assertEqual(car["draft_kind"], "municipal_issue")
        self.assertEqual(car["event_type"], "road_damage")
        # Registry text is only added for parking reports, even when a
        # registry value is present on the record.
        self.assertNotIn("registry_text", car)


class TestRecordToCarValidationErrorMessages(unittest.TestCase):
    """Test each validation raises correct ValueError with expected message."""

    def test_record_must_be_dict(self):
        with self.assertRaisesRegex(ValueError, "^record must be a JSON object$"):
            record_to_car([])

        with self.assertRaisesRegex(ValueError, "^record must be a JSON object$"):
            record_to_car("not a dict")

    def test_unsupported_schema_version(self):
        with self.assertRaisesRegex(ValueError, "^unsupported schema_version 999$"):
            record_to_car(intake_record(schema_version=999))

        with self.assertRaisesRegex(ValueError, "^unsupported schema_version 2$"):
            record_to_car({"schema_version": 2})

    def test_status_not_ready(self):
        with self.assertRaisesRegex(ValueError, "^status is 'draft'$"):
            record_to_car(intake_record(status="draft"))

        with self.assertRaisesRegex(ValueError, "^status is 'pending'$"):
            record_to_car(intake_record(status="pending"))

    def test_needs_clarification(self):
        with self.assertRaisesRegex(
            ValueError, "^violation needs clarification: האם זו מדרכה או כביש\\?$"
        ):
            record_to_car(
                intake_record(
                    violation={
                        "description": "חונה על המדרכה",
                        "needs_clarification": True,
                        "question": "האם זו מדרכה או כביש?",
                    }
                )
            )

    def test_address_street_missing(self):
        with self.assertRaisesRegex(ValueError, r"^address\.street is missing$"):
            record_to_car(intake_record(address={"street": "", "house_number": "10"}))

    def test_address_house_number_missing(self):
        with self.assertRaisesRegex(
            ValueError,
            r"^address\.house_number is missing; confirm a number or research the nearest numbered address and mark it approximate$",
        ):
            record_to_car(intake_record(address={"street": "רחוב", "house_number": ""}))

    def test_plate_missing_for_parking_report(self):
        with self.assertRaisesRegex(ValueError, "^plate is missing$"):
            record_to_car(intake_record(plate=""))

    def test_datetime_missing(self):
        with self.assertRaisesRegex(ValueError, "^datetime is missing$"):
            record_to_car(intake_record(captured_at=""))

    def test_municipal_issue_without_description(self):
        """Pin exact message 'A municipal issue needs an observed description'."""
        record = intake_record(
            plate="",
            violation={"description": "", "category": "road_damage"},
            municipal_subject={"code": "1015"},
        )
        with self.assertRaisesRegex(
            ValueError,
            "^A municipal issue needs an observed description$",
        ):
            record_to_car(record)


if __name__ == "__main__":
    unittest.main()
