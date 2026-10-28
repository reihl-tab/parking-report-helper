"""Unit tests for the #133 PR A validation helpers of monthly_draft."""

import unittest

from parking_report import monthly as m


def row(**changes):
    base = {
        "folder_id": "ZZ-1",
        "filed_date": "2026-05-03",
        "street": "ZZ street",
        "event_type": "sidewalk",
        "municipal_ref": "",
        "ref_mapping": "",
    }
    base.update(changes)
    return base


class TestCheckMonth(unittest.TestCase):
    def test_valid(self):
        self.assertIsNone(m._check_month("2026-05"))

    def test_shape_then_real_month(self):
        for bad in ("2026-5", "202605", "2026-05-01", "", "2026-0x"):
            with self.assertRaisesRegex(ValueError, "^Invalid month$"):
                m._check_month(bad)
        with self.assertRaisesRegex(ValueError, "month must be in 1..12"):
            m._check_month("2026-13")


class TestRowInMonth(unittest.TestCase):
    def test_in_and_out_of_month(self):
        self.assertIs(m._row_in_month(row(), "2026-05"), True)
        self.assertIs(m._row_in_month(row(), "2026-06"), False)

    def test_error_order(self):
        cases = [
            (
                {k: v for k, v in row().items() if k != "street"},
                "^Missing ledger columns$",
            ),
            (row(street=None), "^Invalid ledger value$"),
            (row(filed_date="2026-5-03", street=None), "^Invalid ledger value$"),
            (row(filed_date="03/05/2026"), "^Invalid filing date$"),
            (row(filed_date="2026-02-30"), "day is out of range"),
        ]
        for bad, message in cases:
            with self.assertRaisesRegex(ValueError, message):
                m._row_in_month(bad, "2026-05")

    def test_other_month_skips_content_checks(self):
        self.assertIs(
            m._row_in_month(row(street="\x01", folder_id=""), "2026-06"), False
        )


class TestValidateSelectedRow(unittest.TestCase):
    def test_returns_stripped_ref_and_records_state(self):
        seen, refs = set(), set()
        self.assertEqual(
            m._validate_selected_row(row(municipal_ref=" 12-3 "), seen, refs), "12-3"
        )
        self.assertEqual((seen, refs), ({"ZZ-1"}, {"12-3"}))
        self.assertEqual(
            m._validate_selected_row(row(folder_id="ZZ-2"), seen, refs), ""
        )
        self.assertEqual((seen, refs), ({"ZZ-1", "ZZ-2"}, {"12-3"}))

    def test_error_order_and_messages(self):
        cases = [
            (row(street="a\u202eb", folder_id=""), "^Control characters in ledger$"),
            (row(municipal_ref="\x7f"), "^Control characters in ledger$"),
            (row(event_type="\x85"), "^Control characters in ledger$"),
            (row(folder_id=" "), "^Missing report identity or classification$"),
            (row(street=""), "^Missing report identity or classification$"),
            (row(event_type="  "), "^Missing report identity or classification$"),
            (row(municipal_ref="12a"), "^Invalid municipal reference$"),
            (row(municipal_ref="1-2-3"), "^Invalid municipal reference$"),
            (row(ref_mapping="verified"), "^Verified mapping requires a reference$"),
        ]
        for bad, message in cases:
            with self.assertRaisesRegex(ValueError, message):
                m._validate_selected_row(bad, set(), set())

    def test_duplicates(self):
        seen, refs = {"ZZ-1"}, set()
        with self.assertRaisesRegex(ValueError, "^Duplicate report identity$"):
            m._validate_selected_row(row(), seen, refs)
        seen, refs = set(), {"7"}
        with self.assertRaisesRegex(
            ValueError, "^Duplicate municipal reference; reconcile"
        ):
            m._validate_selected_row(row(municipal_ref="7"), seen, refs)
        self.assertEqual(seen, {"ZZ-1"})  # id recorded before the reference check

    def test_bad_reference_not_recorded(self):
        seen, refs = set(), set()
        with self.assertRaises(ValueError):
            m._validate_selected_row(row(municipal_ref="x"), seen, refs)
        self.assertEqual(refs, set())

    def test_verified_with_reference_passes(self):
        self.assertEqual(
            m._validate_selected_row(
                row(municipal_ref="5", ref_mapping="verified"), set(), set()
            ),
            "5",
        )


if __name__ == "__main__":
    unittest.main()
