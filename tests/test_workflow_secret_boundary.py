"""Workflow secret trust boundary (regression for #119).

A workflow started by a pull request (pull_request, pull_request_target,
workflow_run) runs a workflow file or code a same-repository PR can influence.
Such a workflow may use only the automatic GITHUB_TOKEN, never a repository
secret such as PROJECT_PAT.

This is a conservative plain-text guard, not a proof: the repo has no YAML
dependency, so instead of parsing every valid YAML shape it errs towards
reporting. Any mention of a PR-style event name outside a comment line counts
as a PR trigger, and any `secrets` reference other than a literal
GITHUB_TOKEN (bracket, dynamic, `toJSON(secrets)`, `secrets: inherit`) counts
as a repository secret. It can over-report; it should not under-report for the
shapes covered by the synthetic fixtures below. Review still owns the rest.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
PR_TRIGGERS = frozenset({"pull_request", "pull_request_target", "workflow_run"})
ALLOWED_SECRETS = frozenset({"GITHUB_TOKEN"})
UNKNOWN_SECRET = "<all-or-dynamic>"

_PR_WORD = re.compile(r"\b(?:pull_request(?:_target)?|workflow_run)\b")
_SECRETS = re.compile(r"\bsecrets\b")
_DOT_NAME = re.compile(r"\s*\.\s*([A-Za-z_][A-Za-z0-9_-]*)")
_BRACKET_NAME = re.compile(r"""\s*\[\s*(['"])([A-Za-z_][A-Za-z0-9_-]*)\1\s*\]""")


def strip_comment_lines(text):
    """Drop whole-line YAML comments only (trailing comments are kept, which
    can only make the guard report more, never less)."""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def is_pr_triggered(text):
    """Fail-closed: any PR-style event name outside a comment line counts."""
    return bool(_PR_WORD.search(strip_comment_lines(text)))


def secrets_used(text):
    """Secret names referenced; non-literal access is reported as UNKNOWN_SECRET.
    Names are upper-cased because GitHub secret names are case-insensitive."""
    body = strip_comment_lines(text)
    found = set()
    for m in _SECRETS.finditer(body):
        rest = body[m.end() :]
        named = _DOT_NAME.match(rest) or _BRACKET_NAME.match(rest)
        if named:
            found.add(named.group(named.lastindex).upper())
        else:
            found.add(UNKNOWN_SECRET)
    return sorted(found)


def violations(text):
    """Secrets a PR-triggered workflow must not see (empty list = safe)."""
    if not is_pr_triggered(text):
        return []
    return [s for s in secrets_used(text) if s not in ALLOWED_SECRETS]


def workflow_triggers(text):
    """Event names under the top-level `on:` key, for the plain shapes this
    repo writes (block mapping, block list, inline list or scalar). Any other
    shape raises ValueError instead of guessing."""
    lines = text.splitlines()
    starts = [
        i for i, ln in enumerate(lines) if re.match(r"""(?:on|'on'|"on")\s*:""", ln)
    ]
    if len(starts) != 1:
        raise ValueError("expected exactly one top-level on: key")
    i = starts[0]
    m = re.fullmatch(r"""(?:on|'on'|"on")\s*:\s*(.*?)\s*(?:#.*)?""", lines[i])
    inline = m.group(1)
    if inline:
        if re.fullmatch(r"[A-Za-z_]+", inline):
            return [inline]
        lm = re.fullmatch(r"\[\s*([A-Za-z_]+(?:\s*,\s*[A-Za-z_]+)*)\s*\]", inline)
        if not lm:
            raise ValueError(f"unsupported inline on: syntax: {inline!r}")
        return sorted(t.strip() for t in lm.group(1).split(","))
    events = []
    for ln in lines[i + 1 :]:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if not ln[0].isspace():
            break
        if ln.startswith(("   ", "\t")):
            continue  # nested under an event
        em = re.fullmatch(r"  ([A-Za-z_]+):(?:\s*(?:\[[^\]]*\])?)?\s*(?:#.*)?", ln)
        lm = re.fullmatch(r"  - ([A-Za-z_]+)\s*(?:#.*)?", ln)
        if em:
            events.append(em.group(1))
        elif lm:
            events.append(lm.group(1))
        else:
            raise ValueError(f"unsupported on: entry: {ln.strip()!r}")
    if not events:
        raise ValueError("on: has no events")
    return sorted(events)


SAFE_ISSUES_ONLY = """name: x
# a comment may mention pull_request without counting
on:
  issues:
    types: [opened]
jobs:
  a:
    steps:
      - with:
          github-token: ${{ secrets.PROJECT_PAT }}
"""


def with_on(on_block, secret_expr="secrets.PROJECT_PAT"):
    return (
        f"name: x\n{on_block}jobs:\n  a:\n    env:\n      T: ${{{{ {secret_expr} }}}}\n"
    )


class ParserTests(unittest.TestCase):
    def test_block_form(self):
        text = with_on("on:\n  issues:\n    types: [opened]\n  pull_request:\n")
        self.assertEqual(workflow_triggers(text), ["issues", "pull_request"])

    def test_inline_list_and_scalar(self):
        self.assertEqual(
            workflow_triggers("on: [pull_request, push]\n"), ["pull_request", "push"]
        )
        self.assertEqual(workflow_triggers("on: push\n"), ["push"])

    def test_block_list_form(self):
        self.assertEqual(
            workflow_triggers("on:\n  - workflow_run\n  - push\njobs: {}\n"),
            ["push", "workflow_run"],
        )

    def test_unsupported_shapes_raise_instead_of_guessing(self):
        for text in (
            "name: x\njobs: {}\n",
            "on: {pull_request: {types: [opened]}}\n",
            'on:\n  "pull_request":\n',
            "on:\n  ? pull_request\n",
            "on:\n  issues:\non:\n  push:\n",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                workflow_triggers(text)


class SecretReferenceTests(unittest.TestCase):
    def test_dot_and_bracket_names(self):
        self.assertEqual(secrets_used("${{ secrets.PROJECT_PAT }}"), ["PROJECT_PAT"])
        self.assertEqual(secrets_used("${{ secrets['PROJECT_PAT'] }}"), ["PROJECT_PAT"])
        self.assertEqual(
            secrets_used('${{ secrets [ "project_pat" ] }}'), ["PROJECT_PAT"]
        )

    def test_dynamic_and_whole_context_access_is_unknown(self):
        for expr in (
            "${{ secrets[format('{0}', 'PROJECT_PAT')] }}",
            "${{ secrets[matrix.name] }}",
            "${{ toJSON(secrets) }}",
            "    secrets: inherit",
        ):
            with self.subTest(expr=expr):
                self.assertEqual(secrets_used(expr), [UNKNOWN_SECRET])

    def test_github_token_in_any_literal_form_is_allowed(self):
        self.assertEqual(
            secrets_used("${{ secrets.github_token }} ${{ secrets['GITHUB_TOKEN'] }}"),
            ["GITHUB_TOKEN"],
        )


class BoundaryRuleTests(unittest.TestCase):
    def test_issue_only_workflow_may_use_repository_secret(self):
        self.assertEqual(violations(SAFE_ISSUES_ONLY), [])

    def test_every_pr_style_trigger_in_every_shape_is_a_violation(self):
        shapes = (
            "on:\n  {t}:\n    types: [opened]\n",
            "on: [{t}]\n",
            "on: {t}\n",
            "on: {{{t}: {{types: [opened]}}}}\n",
            'on:\n  "{t}":\n',
            "on:\n  - {t}\n",
        )
        for trigger in sorted(PR_TRIGGERS):
            for shape in shapes:
                text = with_on(shape.format(t=trigger))
                with self.subTest(trigger=trigger, shape=shape):
                    self.assertEqual(violations(text), ["PROJECT_PAT"])

    def test_bracket_and_dynamic_secrets_on_pr_trigger_are_violations(self):
        on = "on: [pull_request]\n"
        self.assertEqual(
            violations(with_on(on, "secrets['PROJECT_PAT']")), ["PROJECT_PAT"]
        )
        self.assertEqual(violations(with_on(on, "toJSON(secrets)")), [UNKNOWN_SECRET])

    def test_github_token_is_allowed_on_pull_requests(self):
        self.assertEqual(
            violations(with_on("on: [pull_request]\n", "secrets.GITHUB_TOKEN")), []
        )


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
        self.assertFalse(is_pr_triggered(text))
        self.assertIn("permissions: {}", text)
        self.assertNotIn("actions/checkout", text)
        self.assertNotRegex(text, r"(?m)^\s*(- )?run:")
        self.assertRegex(text, r"actions/add-to-project@[0-9a-f]{40}\b")


if __name__ == "__main__":
    unittest.main()
