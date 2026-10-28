"""Per-character tests for the monthly_draft control/bidi screen.

Pins behaviour through the public monthly_draft, so these pass before and
after the #133 validation split. Synthetic rows only.
"""

import unittest

from parking_report.monthly import monthly_draft

BIDI = "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
REJECTED = ["\x00", "\x1f", "\x7f", "\x80", "\x9f", *BIDI]
ACCEPTED = [" ", "~", "\xa0", "\u2029", "\u200f", "\u2065", "\u206a"]


def ledger(text, field):
    row = {
        "folder_id": "ZZ-1",
        "filed_date": "2026-05-03",
        "street": "ZZ street",
        "event_type": "sidewalk",
        "municipal_ref": "",
        "ref_mapping": "",
    }
    row[field] = row[field] + text if field != "municipal_ref" else text
    return [row]


class TestControlCharacters(unittest.TestCase):
    def test_each_rejected_character_individually(self):
        for char in REJECTED:
            for field in (
                "folder_id",
                "street",
                "event_type",
                "municipal_ref",
                "ref_mapping",
            ):
                with (
                    self.subTest(char=hex(ord(char)), field=field),
                    self.assertRaisesRegex(
                        ValueError, "^Control characters in ledger$"
                    ),
                ):
                    monthly_draft(ledger(char, field), "2026-05")

    def test_named_boundaries(self):
        # The four boundaries from the #153 review, each on its own.
        for char in ("\x1f", "\x9f", "\u202a", "\u2067"):
            with (
                self.subTest(char=hex(ord(char))),
                self.assertRaisesRegex(ValueError, "^Control characters in ledger$"),
            ):
                monthly_draft(ledger(char, "street"), "2026-05")

    def test_neighbours_are_accepted(self):
        for char in ACCEPTED:
            with self.subTest(char=hex(ord(char))):
                self.assertIn(
                    "ZZ street" + char, monthly_draft(ledger(char, "street"), "2026-05")
                )

    def test_other_month_rows_are_not_screened(self):
        rows = ledger("\x1f", "street")
        rows[0]["filed_date"] = "2026-06-01"
        self.assertIn("אין רשומות", monthly_draft(rows, "2026-05"))


if __name__ == "__main__":
    unittest.main()
