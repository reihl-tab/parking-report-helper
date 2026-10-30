"""State and character pins for status ledger-row validation (#191).

Synthetic rows only. Both pins hold for the current ``_validate_row`` and for
the split in #141, which keeps its signature and mutation order.

- After a reference-validation error, the row's folder_id is already in
  ``ids`` while ``refs`` is unchanged (ID recorded before the ref check).
- DEL (U+007F) in any ledger text is rejected as unsafe; the neighbouring
  accepted characters stay accepted.
"""

import unittest
from datetime import date

from parking_report.status import _validate_row, status_draft

AS_OF = "2026-10-27"
TODAY = date(2026, 10, 27)


def status_row(**changes):
    """A minimal valid ledger row (same shape as test_validate_row_pins_145)."""
    return {
        "folder_id": "gap-001",
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


class TestStateAfterReferenceError(unittest.TestCase):
    def test_invalid_reference_leaves_id_recorded_and_refs_unchanged(self):
        ids, refs = {"earlier-id"}, {"111"}
        with self.assertRaisesRegex(ValueError, "^Invalid or duplicate reference$"):
            _validate_row(status_row(municipal_ref="R-1"), TODAY, ids, refs)
        self.assertEqual(ids, {"earlier-id", "gap-001"})
        self.assertEqual(refs, {"111"})

    def test_duplicate_reference_leaves_id_recorded_and_refs_unchanged(self):
        ids, refs = set(), {"2610-999001"}
        with self.assertRaisesRegex(ValueError, "^Invalid or duplicate reference$"):
            _validate_row(status_row(municipal_ref=" 2610-999001 "), TODAY, ids, refs)
        self.assertEqual(ids, {"gap-001"})
        self.assertEqual(refs, {"2610-999001"})

    def test_same_id_after_reference_error_is_a_duplicate(self):
        ids, refs = set(), set()
        with self.assertRaisesRegex(ValueError, "^Invalid or duplicate reference$"):
            _validate_row(status_row(municipal_ref="R-1"), TODAY, ids, refs)
        with self.assertRaisesRegex(ValueError, "^Duplicate report ID$"):
            _validate_row(status_row(), TODAY, ids, refs)

    def test_later_error_keeps_both_id_and_stripped_reference(self):
        ids, refs = set(), set()
        with self.assertRaisesRegex(ValueError, "^Unknown response status$"):
            _validate_row(
                status_row(municipal_ref=" 2610-999001 ", municipal_response="maybe"),
                TODAY,
                ids,
                refs,
            )
        self.assertEqual(ids, {"gap-001"})
        self.assertEqual(refs, {"2610-999001"})

    def test_success_returns_stripped_reference(self):
        ids, refs = set(), set()
        row, *_ = _validate_row(
            status_row(municipal_ref=" 2610-999001 "), TODAY, ids, refs
        )
        self.assertEqual(row["municipal_ref"], "2610-999001")
        self.assertEqual((ids, refs), ({"gap-001"}, {"2610-999001"}))


class TestDelCharacter(unittest.TestCase):
    def test_del_is_rejected_in_every_text_field(self):
        for field in (
            "plate",
            "street",
            "event_type",
            "response_evidence",
            "x_post_url",
        ):
            row = status_row(**{field: status_row()[field] + "a\x7f"})
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValueError, "^Unsafe ledger value$"),
            ):
                status_draft([row], AS_OF)

    def test_neighbours_keep_their_existing_behaviour(self):
        for char in ("~", "\xa0"):
            with self.subTest(char=hex(ord(char))):
                status_draft([status_row(plate="a" + char)], AS_OF)
        for char in ("\x1f", "\x80", "\x9f"):
            with (
                self.subTest(char=hex(ord(char))),
                self.assertRaisesRegex(ValueError, "^Unsafe ledger value$"),
            ):
                status_draft([status_row(plate="a" + char)], AS_OF)


if __name__ == "__main__":
    unittest.main()
