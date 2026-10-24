"""Historical regression batch1. Public-code tests use synthetic values only."""

import re
import unittest
from datetime import date
from pathlib import Path

from parking_report.registry import BADGES, VEHICLES, describe_he, lookup
from parking_report.report import build_report


def assert_workflow_expressions(text):
    """Guard exact historical#62 template-authoring corruption."""
    if "${{{" in text or "}}}" in text:
        raise ValueError("Malformed workflow expression braces")
    stripped = re.sub(r"\$\{\{[^{}]*\}\}", "", text)
    if "${{" in stripped:
        raise ValueError("Unclosed/nested workflow expression")


class HistoricalWorkflowTests(unittest.TestCase):
    def test_historical_quadruple_brace_workflow_is_rejected(self):
        broken = "name: Coverage report (gate ${{{{ vars.COVERAGE_GATE || 'off' }}}})"
        with self.assertRaises(ValueError):
            assert_workflow_expressions(broken)
        with self.assertRaises(ValueError):
            assert_workflow_expressions("name: ${{ missing")

    def test_current_workflows_have_balanced_expressions_and_static_coverage_name(self):
        root = Path(__file__).resolve().parents[1]
        workflow_dir = root / ".github/workflows"
        if not workflow_dir.is_dir():
            self.skipTest(
                "Source-only workflows are excluded from the Docker test image"
            )
        for file in workflow_dir.glob("*.yml"):
            with self.subTest(workflow=file.name):
                assert_workflow_expressions(file.read_text())
        text = (root / ".github/workflows/tests.yml").read_text()
        self.assertIn("name: Coverage report\n", text)
        self.assertIn("if: vars.COVERAGE_GATE == 'on'", text)


class HistoricalRegistryTests(unittest.TestCase):
    def snapshot(self, test_date, expiry):
        def fetch(resource, field, plate):
            if resource == BADGES:
                return {"total": 0, "records": []}
            self.assertEqual(resource, VEHICLES)
            return {
                "total": 1,
                "records": [
                    {
                        field: int(plate),
                        "tozeret_nm": "Synthetic",
                        "mivchan_acharon_dt": test_date,
                        "tokef_dt": expiry,
                    }
                ],
            }

        return lookup("0000000", date(2026, 10, 22), fetch)

    def test_old_last_test_does_not_mean_expired_licence(self):
        result = self.snapshot("2026-01-02", "2027-01-07")
        self.assertEqual(result["licence_status"], "valid_on_check_date")
        text = describe_he(result)
        self.assertIn("מבחן רישוי אחרון: 2.1.2026", text)
        self.assertIn("תוקף רישיון הרכב עד 7.1.2027 (בתוקף)", text)
        self.assertNotIn("אין לרכב רישיון בתוקף", text)

    def test_recent_last_test_cannot_override_expired_registration(self):
        result = self.snapshot("2026-10-21", "2026-10-20")
        self.assertEqual(result["licence_status"], "expired_on_check_date")
        self.assertIn("אין לרכב רישיון בתוקף", describe_he(result))
        self.assertIn("מבחן רישוי אחרון: 21.10.2026", describe_he(result))

    def test_missing_validity_not_filled_from_last_test(self):
        result = self.snapshot("2026-10-21", None)
        self.assertEqual(result["licence_status"], "unknown")
        self.assertNotIn("רישיון בתוקף", describe_he(result))

    def test_exact_expiry_boundary_uses_validity_field(self):
        result = self.snapshot("2026-01-02", "2026-10-22")
        self.assertEqual(result["licence_status"], "valid_on_check_date")

    def test_gps_fields_do_not_enter_municipal_text(self):
        car = {
            "plate": "0000000",
            "street": "רחוב לדוגמה 1",
            "datetime": "2026-10-22 06:00",
            "gps": {"lat": 12.345678, "lon": 98.765432},
            "lat": 12.345678,
            "lon": 98.765432,
        }
        report = build_report(car)
        self.assertNotIn("12.345678", report)
        self.assertNotIn("98.765432", report)
        self.assertNotIn("GPS", report)


if __name__ == "__main__":
    unittest.main()
