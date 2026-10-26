"""Workflow secret trust boundary (regression for #119).

A workflow started by a pull request (pull_request, pull_request_target,
workflow_run) runs a workflow file or code a same-repository PR can influence.
Such a workflow may use only the automatic GITHUB_TOKEN, never a repository
secret such as PROJECT_PAT. Parsing is plain text so the test needs no YAML
dependency; the fixtures below are synthetic.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
PR_TRIGGERS = frozenset({"pull_request", "pull_request_target", "workflow_run"})
ALLOWED_SECRETS = frozenset({"GITHUB_TOKEN"})


def workflow_triggers(text):
    """Event names under the top-level `on:` key (inline list or block form)."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.fullmatch(r"""["']?on["']?:\s*(.*?)\s*(#.*)?""", line)
        if not m:
            continue
        inline = m.group(1)
        if inline:
            return sorted(
                t.strip().strip("'\"")
                for t in inline.strip("[]").split(",")
                if t.strip()
            )
        events = []
        for ln in lines[i + 1 :]:
            if ln.strip() and not ln[0].isspace() and not ln.startswith("#"):
                break
            em = re.fullmatch(r"  ([A-Za-z_]+):.*", ln)
            if em:
                events.append(em.group(1))
            elif re.fullmatch(r"  - ([A-Za-z_]+)\s*", ln):
                events.append(ln.strip()[2:].strip())
        return sorted(events)
    raise ValueError("workflow has no top-level on: key")


def secrets_used(text):
    return sorted(set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text)))


def violations(text):
    """Secrets a PR-triggered workflow must not see (empty list = safe)."""
    if not set(workflow_triggers(text)) & PR_TRIGGERS:
        return []
    return [s for s in secrets_used(text) if s not in ALLOWED_SECRETS]


SAFE_ISSUES_ONLY = """name: x
on:
  issues:
    types: [opened]
jobs:
  a:
    steps:
      - with:
          github-token: ${{ secrets.PROJECT_PAT }}
"""

UNSAFE_PR = SAFE_ISSUES_ONLY.replace(
    "on:\n  issues:\n    types: [opened]\n",
    "on:\n  issues:\n    types: [opened]\n  pull_request:\n    types: [opened]\n",
)


class ParserTests(unittest.TestCase):
    def test_block_form(self):
        self.assertEqual(workflow_triggers(UNSAFE_PR), ["issues", "pull_request"])

    def test_inline_list_form(self):
        self.assertEqual(
            workflow_triggers("on: [pull_request, push]\njobs: {}\n"),
            ["pull_request", "push"],
        )

    def test_inline_single_and_quoted_key(self):
        self.assertEqual(
            workflow_triggers('"on": pull_request_target\n'), ["pull_request_target"]
        )

    def test_block_list_form(self):
        self.assertEqual(
            workflow_triggers("on:\n  - workflow_run\n  - push\njobs: {}\n"),
            ["push", "workflow_run"],
        )

    def test_missing_on_key_fails_loudly(self):
        with self.assertRaises(ValueError):
            workflow_triggers("name: x\njobs: {}\n")


class BoundaryRuleTests(unittest.TestCase):
    def test_issue_only_workflow_may_use_repository_secret(self):
        self.assertEqual(violations(SAFE_ISSUES_ONLY), [])

    def test_pr_trigger_with_repository_secret_is_a_violation(self):
        self.assertEqual(violations(UNSAFE_PR), ["PROJECT_PAT"])

    def test_every_pr_style_trigger_is_covered(self):
        for trigger in sorted(PR_TRIGGERS):
            text = f"on:\n  {trigger}:\njobs:\n  a:\n    env:\n      T: ${{{{ secrets.PROJECT_PAT }}}}\n"
            with self.subTest(trigger=trigger):
                self.assertEqual(violations(text), ["PROJECT_PAT"])

    def test_github_token_is_allowed_on_pull_requests(self):
        text = "on: [pull_request]\njobs:\n  a:\n    env:\n      T: ${{ secrets.GITHUB_TOKEN }}\n"
        self.assertEqual(violations(text), [])


class RepositoryWorkflowTests(unittest.TestCase):
    def setUp(self):
        if not WORKFLOWS.is_dir():
            # Same reason string as the other workflow-source tests, so the CI
            # summary explains where this check runs instead.
            self.skipTest(
                "Source-only workflows are excluded from the Docker test image"
            )

    def workflow_files(self):
        files = sorted(WORKFLOWS.glob("*.y*ml"))
        self.assertTrue(files, "no workflow files found")
        return files

    def test_no_pr_triggered_workflow_receives_a_repository_secret(self):
        for path in self.workflow_files():
            with self.subTest(workflow=path.name):
                self.assertEqual(violations(path.read_text(encoding="utf-8")), [])

    def test_add_to_project_is_issue_only_and_least_privilege(self):
        text = (WORKFLOWS / "add-to-project.yml").read_text(encoding="utf-8")
        self.assertEqual(workflow_triggers(text), ["issues"])
        self.assertIn("permissions: {}", text)
        self.assertNotIn("actions/checkout", text)
        self.assertNotRegex(text, r"(?m)^\s*(- )?run:")
        self.assertRegex(text, r"actions/add-to-project@[0-9a-f]{40}\b")


if __name__ == "__main__":
    unittest.main()
