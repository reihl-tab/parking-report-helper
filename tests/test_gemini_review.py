"""Offline guard for the Gemini review output sanitizer and diff parser.

No network, no secrets: the cases below are the injection bypasses the
advisory-review design must keep closed (mentions, autolinks in any
case or with any prefix, raw HTML, nested brackets, runner workflow
commands, planted ``+++ b/`` diff paths). A regression here means the
bot could post a live mention or link written by a pull request.
"""

import importlib.util
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "gemini_review.py"
# The Docker test stage copies tests/ without tools/, like the other
# guards here: skip cleanly there instead of failing test discovery.
if SCRIPT.is_file():
    spec = importlib.util.spec_from_file_location("gemini_review", SCRIPT)
    gemini_review = importlib.util.module_from_spec(spec)
    sys.modules["gemini_review"] = gemini_review
    spec.loader.exec_module(gemini_review)
else:
    gemini_review = None


@unittest.skipIf(gemini_review is None, "tools/ is not in the Docker test stage")
class SanitizeModelOutputTest(unittest.TestCase):
    def sanitize(self, text, limit=600):
        return gemini_review.sanitize_model_output(text, limit)

    def test_plain_mention_is_broken(self):
        self.assertIn("@\u200breihl", self.sanitize("@reihl"))

    def test_entity_mentions_are_broken(self):
        for entity in ("&#64;name", "&#0064;name", "&#x40;name", "&commat;name"):
            self.assertIn("@\u200b", self.sanitize(entity), entity)

    def test_urls_never_autolink_whatever_the_prefix(self):
        for prefix in ("", "_", "1", "é", "foo_", "(_", "x", "*", "~"):
            for url in (
                "https://evil.example/x",
                "HTTPS://E.EXAMPLE",
                "ftp://f.example",
                "www.evil.example",
                "WWW.E.EXAMPLE",
            ):
                out = self.sanitize(prefix + url)
                self.assertIsNone(
                    re.search(r"(?i)(?:https?|ftp)://|www\.", out), (prefix, url, out)
                )

    def test_markdown_and_html_render_literally(self):
        out = self.sanitize("[click](https://evil.example)")
        self.assertIn("\\[", out)
        self.assertNotIn("](https", out)
        self.assertIn("\\<a", self.sanitize("<a href='//evil.example'>x</a>"))
        nested = self.sanitize("[[a](b)](c)")
        self.assertEqual(2, nested.count("\\["))
        self.assertEqual(2, nested.count("\\]"))

    def test_workflow_commands_are_defused(self):
        self.assertNotIn("::", self.sanitize("::error::boom::"))

    def test_endorsement_phrase_is_replaced(self):
        self.assertNotIn("safe to merge", self.sanitize("safe to merge").lower())

    def test_length_is_capped(self):
        self.assertLessEqual(len(self.sanitize("y" * 2000)), 600)


@unittest.skipIf(gemini_review is None, "tools/ is not in the Docker test stage")
class AddedLinesTest(unittest.TestCase):
    DIFF = (
        "diff --git a/real.py b/real.py\n"
        "index 111..222 100644\n"
        "--- a/real.py\n"
        "+++ b/real.py\n"
        "@@ -1,1 +1,3 @@\n"
        " keep\n"
        "+new line\n"
        "+++ b/@evil-user](https://evil.example\n"  # added content line
        "+after\n"
    )

    def test_real_header_paths_only(self):
        valid = gemini_review.added_lines(self.DIFF)
        self.assertEqual(["real.py"], list(valid))
        # The planted "+++ b/..." line is content of real.py, not a path.
        self.assertEqual({2, 3, 4}, valid["real.py"])

    def test_quoted_paths_are_skipped(self):
        diff = (
            'diff --git "a/spa ce.py" "b/spa ce.py"\n'
            '--- "a/spa ce.py"\n+++ "b/spa ce.py"\n@@ -0,0 +1,1 @@\n+x\n'
        )
        self.assertEqual({}, gemini_review.added_lines(diff))

    def test_finding_with_markup_path_is_dropped(self):
        valid = gemini_review.added_lines(self.DIFF)
        raw = {
            "findings": [
                {"path": "real.py", "line": 2, "comment": "ok", "severity": "high"},
                {"path": "real.py` @x", "line": 2, "comment": "x", "severity": "low"},
            ]
        }
        kept = gemini_review.validate_findings(raw, valid, 10)
        self.assertEqual(["real.py"], [finding["path"] for finding in kept])


if __name__ == "__main__":
    unittest.main()
