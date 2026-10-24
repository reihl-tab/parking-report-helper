"""Static privacy guards for the manual workflow template."""

import unittest
from pathlib import Path


class TestPrivateRunner(unittest.TestCase):
    def test_no_automatic_events_credentials_or_submission(self):
        source = (Path(__file__).parents[1] / "docs/private-runner.yml").read_text()
        self.assertIn("workflow_dispatch:", source)
        self.assertNotIn("  push:", source)
        self.assertNotIn("  pull_request:", source)
        self.assertNotIn("secrets.", source)
        self.assertNotIn("--fill", source)
        self.assertIn("github.event.repository.private == true", source)
        self.assertIn("REVIEWED_HELPER: 'false'", source)
        self.assertIn("contents: read", source)
        self.assertIn("--network none", source)
        self.assertIn("/data/reports:ro", source)
        self.assertIn("> output/drafts.txt 2> output/diagnostics.txt", source)
        self.assertIn("retention-days: 7", source)
        self.assertIn("persist-credentials: false", source)
