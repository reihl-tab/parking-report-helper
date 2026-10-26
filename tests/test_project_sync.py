"""Label -> project-board sync. Synthetic events and a fake GraphQL transport;
no network, no token."""

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "project_sync", ROOT / "scripts/project_sync.py"
)
ps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ps)

CONFIG = json.loads((ROOT / ".github/project-sync.json").read_text(encoding="utf-8"))
BOARD = [
    {
        "id": "F_status",
        "name": "Status",
        "options": [
            {"id": "O_todo", "name": "Todo"},
            {"id": "O_prog", "name": "In Progress"},
            {"id": "O_done", "name": "Done"},
        ],
    },
    {
        "id": "F_prio",
        "name": "Priority",
        "options": [
            {"id": "O_high", "name": "High"},
            {"id": "O_med", "name": "Medium"},
        ],
    },
    {},  # non-single-select fields come back as empty objects
]


def event(state="open", labels=(), merged=False, kind="issue"):
    item = {
        "node_id": "I_1",
        "state": state,
        "labels": [{"name": n} for n in labels],
    }
    if kind == "pull_request":
        item["merged"] = merged
    return {kind: item}


class FakeGraphQL:
    def __init__(self, items=()):
        self.calls = []
        self.items = list(items)

    def __call__(self, query, variables):
        self.calls.append((query, dict(variables)))
        if "projectV2(number" in query:
            return {"user": {"projectV2": {"id": "P_1", "fields": {"nodes": BOARD}}}}
        if "projectItems" in query:
            return {"node": {"projectItems": {"nodes": self.items}}}
        if "addProjectV2ItemById" in query:
            return {"addProjectV2ItemById": {"item": {"id": "PI_new"}}}
        if "updateProjectV2ItemFieldValue" in query:
            return {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "x"}}}
        raise AssertionError(query)

    def sets(self):
        return [v for q, v in self.calls if "updateProjectV2ItemFieldValue" in q]


class DesiredValuesTests(unittest.TestCase):
    def test_label_rules_and_defaults(self):
        cases = [
            ("open", False, [], {"Status": "Todo"}),
            ("open", False, ["status: in-progress"], {"Status": "In Progress"}),
            (
                "open",
                False,
                ["Status: Blocked", "priority: high"],
                {"Status": "Blocked", "Priority": "High"},
            ),
            ("closed", False, ["status: in-progress"], {"Status": "Done"}),
            ("closed", True, [], {"Status": "Done"}),
        ]
        for state, merged, labels, want in cases:
            with self.subTest(state=state, labels=labels):
                got = ps.desired_values(state, merged, labels, CONFIG["fields"])
                self.assertEqual(got, want)

    def test_first_matching_rule_wins(self):
        got = ps.desired_values(
            "open", False, ["status: in-progress", "status: blocked"], CONFIG["fields"]
        )
        self.assertEqual(got["Status"], "Blocked")

    def test_no_priority_label_leaves_priority_unchanged(self):
        got = ps.desired_values("open", False, ["type: fix"], CONFIG["fields"])
        self.assertNotIn("Priority", got)


class ResolveTests(unittest.TestCase):
    def test_missing_option_and_field_are_reported_not_created(self):
        found, problems = ps.resolve(
            {"Status": "Blocked", "Priority": "High", "Iteration": "x"}, BOARD
        )
        self.assertEqual(found, [("Priority", "High", "F_prio", "O_high")])
        self.assertEqual(len(problems), 2)
        self.assertIn("no option 'Blocked'", problems[0])
        self.assertIn("no single-select field 'Iteration'", problems[1])

    def test_case_insensitive_match(self):
        found, _ = ps.resolve({"status": "in progress"}, BOARD)
        self.assertEqual(found[0][2:], ("F_status", "O_prog"))


class SyncTests(unittest.TestCase):
    def run_sync(self, ev, gql, apply):
        out = []
        rc = ps.sync(ev, CONFIG, gql, apply, log=out.append)
        return rc, out

    def test_dry_run_reads_but_never_writes(self):
        gql = FakeGraphQL(items=[{"id": "PI_1", "project": {"id": "P_1"}}])
        rc, out = self.run_sync(event(labels=["status: in-progress"]), gql, apply=False)
        self.assertEqual(rc, 0)
        self.assertEqual(gql.sets(), [])
        self.assertFalse(any("addProjectV2ItemById" in q for q, _ in gql.calls))
        self.assertIn("dry run: would set Status = In Progress", out)

    def test_apply_sets_fields_on_existing_item(self):
        gql = FakeGraphQL(items=[{"id": "PI_1", "project": {"id": "P_1"}}])
        rc, _ = self.run_sync(
            event(labels=["status: in-progress", "priority: high"]), gql, apply=True
        )
        self.assertEqual(rc, 0)
        self.assertEqual(
            [(v["field"], v["option"], v["item"]) for v in gql.sets()],
            [("F_status", "O_prog", "PI_1"), ("F_prio", "O_high", "PI_1")],
        )

    def test_apply_adds_missing_item_first(self):
        gql = FakeGraphQL(items=[{"id": "PI_other", "project": {"id": "P_other"}}])
        self.run_sync(event(), gql, apply=True)
        self.assertEqual(gql.sets()[0]["item"], "PI_new")

    def test_merged_pr_goes_to_done(self):
        gql = FakeGraphQL(items=[{"id": "PI_1", "project": {"id": "P_1"}}])
        self.run_sync(
            event(state="closed", merged=True, kind="pull_request"), gql, apply=True
        )
        self.assertEqual(gql.sets()[0]["option"], "O_done")

    def test_missing_board_option_is_skipped_with_warning(self):
        gql = FakeGraphQL(items=[{"id": "PI_1", "project": {"id": "P_1"}}])
        rc, out = self.run_sync(event(labels=["status: blocked"]), gql, apply=True)
        self.assertEqual(rc, 0)
        self.assertEqual(gql.sets(), [])
        self.assertTrue(any(line.startswith("::warning::") for line in out))

    def test_without_token_plans_only(self):
        rc, out = self.run_sync(event(), None, apply=True)
        self.assertEqual(rc, 0)
        self.assertIn("no PROJECT_PAT available: plan only", out)

    def test_event_without_subject_is_a_noop(self):
        rc, _ = self.run_sync({"action": "x"}, FakeGraphQL(), apply=True)
        self.assertEqual(rc, 0)


class WorkflowContractTests(unittest.TestCase):
    def test_workflow_is_least_privilege_and_keeps_secrets_in_env(self):
        text = (ROOT / ".github/workflows/project-sync.yml").read_text(encoding="utf-8")
        self.assertIn("permissions: {}", text)
        self.assertNotIn("pull_request_target", text)
        run_lines = [ln for ln in text.splitlines() if ln.strip().startswith("run:")]
        self.assertEqual(run_lines, ["        run: python scripts/project_sync.py"])
        self.assertIn("PROJECT_PAT: ${{ secrets.PROJECT_PAT }}", text)


if __name__ == "__main__":
    unittest.main()
