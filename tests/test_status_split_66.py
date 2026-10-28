"""Direct tests for the status_draft helpers split out in #66 PR 1.

status_draft is covered end to end by tests/test_characterize_66.py; these
tests pin each helper's own contract so later changes to one part are
caught where they happen.
"""

import unittest
from datetime import date

from parking_report.status import (
    _classify_status,
    _format_output,
    _validate_row,
    status_draft,
)

TODAY = date(2026, 10, 27)

EMPTY_DRAFT_LINES = (
    "# טיוטת מצב פרטית ליום 2026-10-27",
    "",
    "לבדיקה בלבד. אין שליחה אוטומטית. נמענים ונוסח סופי דורשים אישור.",
    "מועמד לתזכורת רק אחרי שבעה ימים, בדיקת מענה עדכנית ושיוך פנייה מאומת ביומן.",
    "היעדר שדה מענה אינו הוכחה שלא התקבל מענה. אישור פתיחת פנייה אינו מענה לטיפול.",
    "",
    "| מזהה דיווח | תאריך | רחוב | לוחית (פרטי) | מספר פנייה | שיוך | X | Facebook | מענה | מצב |",
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    "",
    "## טיוטות תזכורת: 0",
    "",
    "יש לבדוק שוב את המענה ואת מקור שיוך הפנייה לפני שליחה; הקוד קורא סימונים, לא מאמת מקורות.",
)


def status_row(**changes):
    """A minimal valid ledger row (same shape as test_characterize_66)."""
    return {
        "folder_id": "split-001",
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


def classified(**changes):
    """Validate a row against TODAY, then classify it."""
    row, filed, checked, reminded = _validate_row(
        status_row(**changes), TODAY, set(), set()
    )
    return _classify_status(row, filed, checked, reminded, TODAY)


class TestValidateRow(unittest.TestCase):
    def test_returns_normalised_row_and_dates(self):
        ids, refs = set(), set()
        result = _validate_row(
            status_row(municipal_ref="  2610-999001 ", last_reminder_at="2026-10-21"),
            TODAY,
            ids,
            refs,
        )
        expected_row = status_row(
            municipal_ref="2610-999001", last_reminder_at="2026-10-21"
        )
        self.assertEqual(
            result,
            (expected_row, date(2026, 10, 20), date(2026, 10, 27), date(2026, 10, 21)),
        )
        self.assertEqual(ids, {"split-001"})
        self.assertEqual(refs, {"2610-999001"})

    def test_missing_optional_fields_become_empty_and_dates_none(self):
        raw = {
            key: value
            for key, value in status_row(
                municipal_response="", response_checked_at="", response_evidence=""
            ).items()
            if key
            in {
                "folder_id",
                "filed_date",
                "street",
                "event_type",
                "municipal_ref",
                "ref_mapping",
            }
        }
        row, filed, checked, reminded = _validate_row(raw, TODAY, set(), set())
        self.assertEqual(row["plate"], "")
        self.assertEqual(row["municipal_response"], "")
        self.assertEqual((filed, checked, reminded), (date(2026, 10, 20), None, None))

    def test_empty_ref_is_not_recorded(self):
        refs = set()
        row, *_ = _validate_row(status_row(municipal_ref="   "), TODAY, set(), refs)
        self.assertEqual(row["municipal_ref"], "")
        self.assertEqual(refs, set())

    def test_shared_sets_detect_duplicates_across_calls(self):
        ids, refs = set(), set()
        _validate_row(status_row(), TODAY, ids, refs)
        with self.assertRaisesRegex(ValueError, "^Duplicate report ID$"):
            _validate_row(status_row(), TODAY, ids, refs)
        with self.assertRaisesRegex(ValueError, "^Invalid or duplicate reference$"):
            _validate_row(status_row(folder_id="split-002"), TODAY, ids, refs)

    def test_rejects_bidi_control_character(self):
        with self.assertRaisesRegex(ValueError, "^Unsafe ledger value$"):
            _validate_row(status_row(street="רחוב\u202e"), TODAY, set(), set())


class TestClassifyStatus(unittest.TestCase):
    """One case per branch, asserting the whole (status, age, mapping, due)."""

    def test_received(self):
        self.assertEqual(
            classified(municipal_response="received"), ("מענה מתועד", 7, True, False)
        )

    def test_unknown(self):
        self.assertEqual(
            classified(
                municipal_response="", response_checked_at="", response_evidence=""
            ),
            ("מענה לא נבדק", 7, True, False),
        )

    def test_check_not_current(self):
        self.assertEqual(
            classified(response_checked_at="2026-10-26"),
            ("נדרשת בדיקת מענה עדכנית", 7, True, False),
        )

    def test_under_seven_days(self):
        self.assertEqual(
            classified(filed_date="2026-10-21"), ("טרם חלף שבוע", 6, True, False)
        )

    def test_already_reminded_since_check(self):
        self.assertEqual(
            classified(last_reminder_at="2026-10-27"),
            ("כבר נשלחה תזכורת לפי הבדיקה הנוכחית", 7, True, False),
        )

    def test_mapping_not_verified(self):
        self.assertEqual(
            classified(ref_mapping="claimed"),
            ("חלף שבוע; שיוך הפנייה דורש אימות", 7, False, False),
        )

    def test_mapping_needs_receipt_evidence(self):
        self.assertEqual(
            classified(receipt_evidence="  "),
            ("חלף שבוע; שיוך הפנייה דורש אימות", 7, False, False),
        )

    def test_due(self):
        self.assertEqual(classified(), ("מועמד לתזכורת; נדרש אישור", 7, True, True))


class TestFormatOutput(unittest.TestCase):
    def test_empty_report_list(self):
        self.assertEqual(
            _format_output([], "2026-10-27"),
            "\n".join(EMPTY_DRAFT_LINES) + "\n",
        )

    def test_keeps_given_order_and_escapes_values(self):
        first = status_row(folder_id="b", street="a|b", plate="")
        second = status_row(folder_id="a", municipal_ref="")
        out = _format_output(
            [
                (first, "S1", 7, True, True),
                (second, "S2", 3, False, False),
            ],
            "2026-10-27",
        )
        rows = [line for line in out.splitlines() if line.startswith("| ")][2:]
        self.assertEqual(
            rows,
            [
                "| b | 2026-10-20 | a\\|b | חסר | 2610-999001 | מאומת ביומן | חסר | חסר | ללא מענה לפי בדיקה | S1 |",
                "| a | 2026-10-20 | רחוב הבדיקה | 12-345-67 | חסר | לא מאומת | חסר | חסר | ללא מענה לפי בדיקה | S2 |",
            ],
        )
        self.assertTrue(
            out.endswith(
                "### פנייה 2610-999001\n\n"
                "שלום, אבקש עדכון על פנייה 2610-999001 שנפתחה בתאריך 2026-10-20. תודה.\n"
            )
        )
        self.assertIn("## טיוטות תזכורת: 1\n", out)


class TestStatusDraftComposition(unittest.TestCase):
    def test_status_draft_equals_helpers_composed(self):
        rows = [
            status_row(folder_id="z", filed_date="2026-10-22"),
            status_row(folder_id="y", municipal_ref="2610-999002"),
        ]
        reports = []
        ids, refs = set(), set()
        for raw in rows:
            row, filed, checked, reminded = _validate_row(raw, TODAY, ids, refs)
            reports.append(
                (row, *_classify_status(row, filed, checked, reminded, TODAY))
            )
        reports.sort(key=lambda item: (item[0]["filed_date"], item[0]["folder_id"]))
        self.assertEqual(
            status_draft(rows, "2026-10-27"), _format_output(reports, "2026-10-27")
        )


if __name__ == "__main__":
    unittest.main()
