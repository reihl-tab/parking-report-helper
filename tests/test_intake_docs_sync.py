"""The two intake guides must list the same required report.json fields (#152)."""

import re
import unittest
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs"


def required_fields(name):
    text = (DOCS / name).read_text(encoding="utf-8")
    lines = [
        line for line in text.splitlines() if line.startswith("- Required for use:")
    ]
    if len(lines) != 1:
        raise AssertionError(f"{name}: expected one 'Required for use' line")
    return re.findall(r"`([^`]+)`", lines[0])


class IntakeDocsSyncTests(unittest.TestCase):
    def test_guides_list_the_same_required_fields(self):
        self.assertEqual(
            required_fields("GITHUB_INTAKE.md"), required_fields("INTAKE.md")
        )

    def test_house_number_is_required_in_both(self):
        # intake._validate_record rejects a missing address.house_number.
        for name in ("INTAKE.md", "GITHUB_INTAKE.md"):
            with self.subTest(guide=name):
                self.assertIn("address.house_number", required_fields(name))


if __name__ == "__main__":
    unittest.main()
