"""Characterization tests for monthly_draft (#133, step 1).

These pin the CURRENT behaviour of parking_report.monthly.monthly_draft
before any refactor: exact output, every validation error and its order,
and the month filter. They must pass on unchanged src/.
"""

import unittest

from parking_report.monthly import monthly_draft


def row(**changes):
    """A minimal valid ledger row for monthly_draft with optional overrides."""
    return {
        "folder_id": "m-001",
        "filed_date": "2026-10-05",
        "street": "רחוב הבדיקה",
        "event_type": "sidewalk",
        "municipal_ref": "2610-100",
        "ref_mapping": "verified",
    } | changes


def sample_rows():
    return [
        row(),
        row(
            folder_id="m-002",
            filed_date="2026-10-03",
            street=" רחוב הבדיקה ",
            event_type="crossing",
            municipal_ref=" 2610-101 ",
            ref_mapping="claimed",
        ),
        row(
            folder_id="m-003",
            filed_date="2026-10-03",
            street="שדרה|א",
            municipal_ref="",
            ref_mapping="",
        ),
        # Another month: skipped before the identity and reference checks.
        row(folder_id="m-004", filed_date="2026-09-30", street="", municipal_ref="x"),
    ]


SNAPSHOT_LINES = (
    "# טיוטה: סיכום דיווחים לחודש 2026-10",
    "",
    "טיוטה לבדיקה בלבד. הנמענים והנוסח דורשים אישור לפני שליחה.",
    "",
    "רשומות דיווח ביומן: 3",
    "שיוך מספר פנייה מאומת: 1",
    "שיוך חסר או לא מאומת: 2",
    "",
    "הספירה היא של אירועי דיווח ביומן, לא של כלי רכב ייחודיים או תוצאות אכיפה.",
    "רחובות וסוגי אירוע נשענים על היומן; שיוך פנייה לא מאומת אינו מאשר את פרטי הפנייה בעירייה.",
    "",
    "## לפי רחוב",
    "",
    "| רחוב | דיווחים |",
    "| --- | ---: |",
    "| רחוב הבדיקה | 2 |",
    "| שדרה\\|א | 1 |",
    "",
    "## לפי סוג אירוע",
    "",
    "| סוג אירוע | דיווחים |",
    "| --- | ---: |",
    "| sidewalk | 2 |",
    "| crossing | 1 |",
    "",
    "## מספרי פנייה",
    "",
    "| תאריך | רחוב | סוג אירוע | מספר פנייה | שיוך |",
    "| --- | --- | --- | --- | --- |",
    "| 2026-10-03 |  רחוב הבדיקה  | crossing | 2610-101 | לא מאומת |",
    "| 2026-10-03 | שדרה\\|א | sidewalk | חסר | לא מאומת |",
    "| 2026-10-05 | רחוב הבדיקה | sidewalk | 2610-100 | מאומת |",
)

EMPTY_LINES = (
    "# טיוטה: סיכום דיווחים לחודש 2026-10",
    "",
    "טיוטה לבדיקה בלבד. הנמענים והנוסח דורשים אישור לפני שליחה.",
    "",
    "רשומות דיווח ביומן: 0",
    "שיוך מספר פנייה מאומת: 0",
    "שיוך חסר או לא מאומת: 0",
    "",
    "הספירה היא של אירועי דיווח ביומן, לא של כלי רכב ייחודיים או תוצאות אכיפה.",
    "רחובות וסוגי אירוע נשענים על היומן; שיוך פנייה לא מאומת אינו מאשר את פרטי הפנייה בעירייה.",
    "",
    "## לפי רחוב",
    "",
    "| רחוב | דיווחים |",
    "| --- | ---: |",
    "",
    "## לפי סוג אירוע",
    "",
    "| סוג אירוע | דיווחים |",
    "| --- | ---: |",
    "",
    "## מספרי פנייה",
    "",
    "| תאריך | רחוב | סוג אירוע | מספר פנייה | שיוך |",
    "| --- | --- | --- | --- | --- |",
    "",
    "אין רשומות בחודש הנבחר ביומן שסופק.",
)


class TestMonthlyDraftOutput(unittest.TestCase):
    def test_full_snapshot(self):
        self.assertEqual(
            monthly_draft(sample_rows(), "2026-10"), "\n".join(SNAPSHOT_LINES) + "\n"
        )

    def test_empty_month_snapshot(self):
        self.assertEqual(monthly_draft([], "2026-10"), "\n".join(EMPTY_LINES) + "\n")

    def test_only_other_months_is_empty_output(self):
        self.assertEqual(
            monthly_draft([row(filed_date="2026-09-30")], "2026-10"),
            "\n".join(EMPTY_LINES) + "\n",
        )

    def test_counts_strip_street_but_table_row_keeps_raw_street(self):
        # Current behaviour: the per-street count uses the stripped value, the
        # reference table prints the cell as stored (only escaped).
        out = monthly_draft(
            [
                row(),
                row(
                    folder_id="m-002",
                    street=" רחוב הבדיקה ",
                    municipal_ref="",
                    ref_mapping="claimed",
                ),
            ],
            "2026-10",
        )
        self.assertIn("| רחוב הבדיקה | 2 |\n", out)
        self.assertIn("|  רחוב הבדיקה  |", out)

    def test_ties_sorted_by_name_and_counts_descending(self):
        rows = [
            row(
                folder_id="a",
                street="ב",
                event_type="y",
                municipal_ref="",
                ref_mapping="",
            ),
            row(
                folder_id="b",
                street="א",
                event_type="x",
                municipal_ref="",
                ref_mapping="",
            ),
            row(
                folder_id="c",
                street="ב",
                event_type="x",
                municipal_ref="",
                ref_mapping="",
            ),
        ]
        lines = monthly_draft(rows, "2026-10").splitlines()
        start = lines.index("## לפי רחוב")
        self.assertEqual(lines[start + 4 : start + 6], ["| ב | 2 |", "| א | 1 |"])
        start = lines.index("## לפי סוג אירוע")
        self.assertEqual(lines[start + 4 : start + 6], ["| x | 2 |", "| y | 1 |"])

    def test_reference_table_sorted_by_date_then_folder_id(self):
        rows = [
            row(folder_id="m-9", filed_date="2026-10-02", municipal_ref="9"),
            row(folder_id="m-2", filed_date="2026-10-02", municipal_ref="2"),
            row(folder_id="m-1", filed_date="2026-10-07", municipal_ref="1"),
        ]
        refs = [
            line.split(" | ")[3]
            for line in monthly_draft(rows, "2026-10").splitlines()
            if line.startswith("| 2026-")
        ]
        self.assertEqual(refs, ["2", "9", "1"])

    def test_only_exact_verified_counts_as_verified(self):
        rows = [
            row(folder_id="a", municipal_ref="1", ref_mapping="verified_receipt"),
            row(folder_id="b", municipal_ref="2", ref_mapping="Verified"),
        ]
        out = monthly_draft(rows, "2026-10")
        self.assertIn("שיוך מספר פנייה מאומת: 0\n", out)
        self.assertIn("שיוך חסר או לא מאומת: 2\n", out)

    def test_cell_escaping(self):
        out = monthly_draft(
            [
                row(
                    street="a\\b|c<d>e",
                    event_type="x|y",
                    municipal_ref="",
                    ref_mapping="",
                )
            ],
            "2026-10",
        )
        self.assertIn("| a\\\\b\\|c&lt;d&gt;e | 1 |", out)
        self.assertIn("| x\\|y | 1 |", out)

    def test_extra_columns_are_ignored(self):
        self.assertEqual(
            monthly_draft([row(plate="12-345-67", notes="x")], "2026-10"),
            monthly_draft([row()], "2026-10"),
        )


class TestMonthlyDraftValidation(unittest.TestCase):
    def assertRejects(self, message, rows, month="2026-10"):
        with self.assertRaises(ValueError) as caught:
            monthly_draft(rows, month)
        self.assertEqual(str(caught.exception), message)

    def test_month_format(self):
        for month in ("2026-1", "26-10", "2026-10-01", "", "2026/10"):
            with self.subTest(month=month):
                self.assertRejects("Invalid month", [], month)

    def test_month_out_of_range(self):
        self.assertRejects("month must be in 1..12", [], "2026-13")

    def test_month_checked_before_rows(self):
        self.assertRejects("Invalid month", [{"bad": "row"}], "2026-1")

    def test_missing_columns(self):
        bad = row()
        del bad["ref_mapping"]
        self.assertRejects("Missing ledger columns", [bad])

    def test_non_string_value(self):
        self.assertRejects("Invalid ledger value", [row(municipal_ref=None)])

    def test_filing_date_format(self):
        self.assertRejects("Invalid filing date", [row(filed_date="2026-10-5")])

    def test_filing_date_impossible(self):
        self.assertRejects(
            "day is out of range for month", [row(filed_date="2026-02-30")], "2026-02"
        )

    def test_other_month_rows_still_need_columns_and_dates(self):
        self.assertRejects(
            "Invalid filing date", [row(filed_date="2026-09-3")], "2026-10"
        )

    def test_control_characters(self):
        for value in ("a\x00", "a\x7f", "a\u202e", "a\u2066"):
            with self.subTest(value=value):
                self.assertRejects("Control characters in ledger", [row(street=value)])

    def test_missing_identity(self):
        for key in ("folder_id", "street", "event_type"):
            with self.subTest(key=key):
                self.assertRejects(
                    "Missing report identity or classification", [row(**{key: "  "})]
                )

    def test_duplicate_folder_id(self):
        self.assertRejects(
            "Duplicate report identity", [row(), row(municipal_ref="2610-200")]
        )

    def test_duplicate_folder_id_in_other_month_is_not_checked(self):
        out = monthly_draft([row(), row(filed_date="2026-09-01")], "2026-10")
        self.assertIn("רשומות דיווח ביומן: 1\n", out)

    def test_invalid_reference(self):
        for ref in ("x1", "2610-", "26-10-1"):
            with self.subTest(ref=ref):
                self.assertRejects(
                    "Invalid municipal reference", [row(municipal_ref=ref)]
                )

    def test_duplicate_reference_after_strip(self):
        self.assertRejects(
            "Duplicate municipal reference; reconcile the ledger",
            [row(), row(folder_id="m-002", municipal_ref=" 2610-100 ")],
        )

    def test_verified_requires_reference(self):
        self.assertRejects(
            "Verified mapping requires a reference", [row(municipal_ref="  ")]
        )

    def test_order_control_characters_before_identity(self):
        self.assertRejects(
            "Control characters in ledger", [row(street=" ", event_type="a\x01")]
        )

    def test_order_identity_before_duplicate(self):
        self.assertRejects(
            "Missing report identity or classification", [row(), row(street="")]
        )


if __name__ == "__main__":
    unittest.main()
