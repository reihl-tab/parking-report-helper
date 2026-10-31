"""Exact-output pins for registry.describe_he (#195, child of #66).

Synthetic lookup() results only. Each case pins the whole Hebrew text, so the
split into helpers cannot change wording, spacing, order or omission rules.
"""

import unittest

from parking_report.registry import describe_he

HEAD = "בדיקה במאגרי משרד התחבורה (נכון ל-27.10.2026):"


def result(**changes):
    return {
        "plate": "1234567",
        "checked_on": "2026-10-27",
        "vehicle_lookup": "matched",
        "licence_status": "valid_on_check_date",
        "vehicle": {
            "tozeret_nm": " טויוטה ",
            "degem_nm": "קורולה",
            "kinuy_mishari": " היבריד ",
            "tzeva_rechev": "לבן",
            "tokef_dt": "2027-01-05T00:00:00",
            "mivchan_acharon_dt": "2026-01-04",
        },
        "disability_lookup": "matched",
        "disability": {"SUG TAV": 0, "TAARICH HAFAKAT TAG": "20250101"},
    } | changes


class TestExactOutput(unittest.TestCase):
    def test_full_matched_result(self):
        self.assertEqual(
            describe_he(result()),
            HEAD
            + " רכב מסוג טויוטה קורולה (היבריד), צבע לבן."
            + " תוקף רישיון הרכב עד 5.1.2027 (בתוקף)."
            + " מבחן רישוי אחרון: 4.1.2026."
            + " לרכב רשום תג נכה במאגר תגי הנכים (קוד סוג תג: 0), הונפק ב-1.1.2025.",
        )

    def test_unverified_licence_and_no_tag(self):
        self.assertEqual(
            describe_he(
                result(
                    licence_status="unknown", disability_lookup="not_found_in_dataset"
                )
            ),
            HEAD
            + " רכב מסוג טויוטה קורולה (היבריד), צבע לבן."
            + " תוקף הרישיון לא אומת."
            + " מבחן רישוי אחרון: 4.1.2026."
            + " לא נמצא לרכב תג נכה במאגר תגי הנכים.",
        )

    def test_unmatched_vehicle_says_nothing_about_it(self):
        self.assertEqual(
            describe_he(
                result(vehicle_lookup="unverified", disability_lookup="unverified")
            ),
            "",
        )
        self.assertEqual(
            describe_he(result(vehicle_lookup="not_found_in_dataset")),
            HEAD
            + " לרכב רשום תג נכה במאגר תגי הנכים (קוד סוג תג: 0), הונפק ב-1.1.2025.",
        )

    def test_padded_colour_and_tag_kind_are_stripped(self):
        padded = result(
            vehicle=result()["vehicle"] | {"tzeva_rechev": "  לבן  "},
            disability={"SUG TAV": "  3  ", "TAARICH HAFAKAT TAG": "20250101"},
        )
        text = describe_he(padded)
        self.assertIn(", צבע לבן.", text)
        self.assertIn("(קוד סוג תג: 3)", text)
        self.assertNotIn(" .", text)
        self.assertNotIn(" )", text)
        self.assertEqual(
            text,
            HEAD
            + " רכב מסוג טויוטה קורולה (היבריד), צבע לבן."
            + " תוקף רישיון הרכב עד 5.1.2027 (בתוקף)."
            + " מבחן רישוי אחרון: 4.1.2026."
            + " לרכב רשום תג נכה במאגר תגי הנכים (קוד סוג תג: 3), הונפק ב-1.1.2025.",
        )


if __name__ == "__main__":
    unittest.main()
