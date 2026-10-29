"""Static privacy guards for the manual workflow template."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


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

    def test_runner_labels_are_pinned(self):
        # #169: the template the private repo copies must match the real
        # workflows (#149/#150), so private runs do not drift onto a new
        # Ubuntu release when ubuntu-latest moves.
        files = [ROOT / "docs/private-runner.yml"]
        files += sorted((ROOT / ".github/workflows").glob("*.y*ml"))
        for path in files:
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                labels = re.findall(
                    r"^\s*runs-on:\s*(\S+)",
                    path.read_text(encoding="utf-8"),
                    re.MULTILINE,
                )
                self.assertTrue(labels, "no runs-on found")
                for label in labels:
                    self.assertNotRegex(label, r"-latest$")
