"""Order and boundary pins for status ledger-row validation (#145).

These go through the public ``status_draft`` so they hold both before and
after the ``_validate_row`` split in #141. Synthetic rows only.

Pinned here (gaps named in #145):
- which ValueError fires first when one row has several defects;
- the future-date boundary: filed == as-of is allowed, as-of + 1 is not;
- both edges of filed <= last_reminder_at <= as-of, and of the
  response check date, are allowed;
- event_type is part of the report identity (blank or whitespace rejects).
"""

import unittest

from parking_report.status import status_draft

AS_OF = "2026-10-27"


def status_row(**changes):
    """A minimal valid ledger row (same shape as test_characterize_66)."""
    return {
        "folder_id": "pin-001",
        "filed_date": "2026-10-20",
        "street": "רחוב הבדיקה",
        "event_type": "sidewalk",
        "municipal_ref": "2610-999001",
        "ref_mapping": "verified_receipt",
        "receipt_evidence": "test-receipt.json",
        "plate": "",
        "x_post_url": "",
        "facebook_post_url": "",
        "municipal_response": "no_reply",
        "response_checked_at": AS_OF,
        "response_evidence": "test-mail",
        "last_reminder_at": "",
    } | changes


# Each check in validation order, with a single-field defect that trips only
# that check on an otherwise valid row. Duplicate ID needs a prior row, so it
# is covered separately below.
DEFECTS = [
    ("^Unsafe ledger value$", {"plate": "x\x1f"}),
    ("^Missing report identity$", {"event_type": " "}),
    ("^Future filing date$", {"filed_date": "2026-10-28"}),
    ("^Invalid or duplicate reference$", {"municipal_ref": "R-1"}),
    ("^Unknown response status$", {"municipal_response": "maybe"}),
    ("^Invalid response check date$", {"response_checked_at": "2026-10-19"}),
    (
        "^Response state requires checked date and source evidence$",
        {"response_evidence": " "},
    ),
    ("^Invalid reminder date$", {"last_reminder_at": "2026-10-28"}),
]


class TestCheckOrder(unittest.TestCase):
    def test_each_defect_alone(self):
        for message, change in DEFECTS:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(ValueError, message),
            ):
                status_draft([status_row(**change)], AS_OF)

    def test_earlier_check_wins_over_every_later_one(self):
        for i, (message, earlier) in enumerate(DEFECTS):
            for later_message, later in DEFECTS[i + 1 :]:
                if earlier.keys() & later.keys():
                    continue
                with (
                    self.subTest(first=message, also=later_message),
                    self.assertRaisesRegex(ValueError, message),
                ):
                    status_draft([status_row(**later, **earlier)], AS_OF)

    def test_missing_columns_wins_over_everything(self):
        row = status_row(**{k: v for _, c in DEFECTS for k, v in c.items()})
        del row["ref_mapping"]
        with self.assertRaisesRegex(ValueError, "^Missing ledger columns$"):
            status_draft([row], AS_OF)

    def test_duplicate_id_comes_after_future_date_and_before_reference(self):
        first = status_row()
        with self.assertRaisesRegex(ValueError, "^Future filing date$"):
            status_draft([first, status_row(filed_date="2026-10-28")], AS_OF)
        with self.assertRaisesRegex(ValueError, "^Duplicate report ID$"):
            status_draft([first, status_row(municipal_ref="R-1")], AS_OF)


class TestDateBoundaries(unittest.TestCase):
    def test_filed_today_is_allowed(self):
        out = status_draft(
            [status_row(filed_date=AS_OF, response_checked_at=AS_OF)], AS_OF
        )
        self.assertIn(f"| pin-001 | {AS_OF} |", out)

    def test_filed_tomorrow_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "^Future filing date$"):
            status_draft([status_row(filed_date="2026-10-28")], AS_OF)

    def test_reminder_on_filing_day_is_allowed(self):
        status_draft([status_row(last_reminder_at="2026-10-20")], AS_OF)

    def test_reminder_today_is_allowed(self):
        status_draft([status_row(last_reminder_at=AS_OF)], AS_OF)

    def test_reminder_one_day_outside_either_edge_is_rejected(self):
        for day in ("2026-10-19", "2026-10-28"):
            with (
                self.subTest(day=day),
                self.assertRaisesRegex(ValueError, "^Invalid reminder date$"),
            ):
                status_draft([status_row(last_reminder_at=day)], AS_OF)

    def test_response_check_on_filing_day_is_allowed(self):
        status_draft([status_row(response_checked_at="2026-10-20")], AS_OF)

    def test_response_check_today_is_allowed(self):
        status_draft([status_row(response_checked_at=AS_OF)], AS_OF)


class TestEventTypeIdentity(unittest.TestCase):
    def test_blank_or_whitespace_event_type_is_rejected(self):
        for value in ("", "   "):
            with (
                self.subTest(value=repr(value)),
                self.assertRaisesRegex(ValueError, "^Missing report identity$"),
            ):
                status_draft([status_row(event_type=value)], AS_OF)

    def test_blank_street_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "^Missing report identity$"):
            status_draft([status_row(street=" ")], AS_OF)


if __name__ == "__main__":
    unittest.main()
