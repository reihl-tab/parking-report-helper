"""Label -> project-board sync. Synthetic events and a fake GraphQL transport;
no network, no token."""

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if not (ROOT / ".github/project-sync.json").is_file():
    # The Docker test stage copies src/tests/scripts/docs but not .github/;
    # the sync is a repository workflow, covered by the regular test matrix.
    raise unittest.SkipTest("repository .github/ not present (container build)")
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
            {"id": "O_p1", "name": "P1"},
            {"id": "O_p2", "name": "P2"},
        ],
    },
    {},  # non-single-select fields come back as empty objects
]


def event(state="open", labels=(), merged=False, kind="issue", action=None, label=None):
    item = {
        "node_id": "I_1",
        "state": state,
        "labels": [{"name": n} for n in labels],
    }
    if kind == "pull_request":
        item["merged"] = merged
    ev = {kind: item}
    if action:
        ev["action"] = action
    if label:
        ev["label"] = {"name": label}
    return ev


def board_item(**values):
    """A project item on P_1 whose single-select fields show `values`."""
    return {
        "id": "PI_1",
        "project": {"id": "P_1"},
        "fieldValues": {
            "nodes": [
                {},  # non-single-select values come back as empty objects
                *(
                    {"name": option, "field": {"name": field}}
                    for field, option in values.items()
                ),
            ]
        },
    }


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
                {"Status": "Blocked", "Priority": "P1"},
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
            {"Status": "Blocked", "Priority": "P1", "Iteration": "x"}, BOARD
        )
        self.assertEqual(found, [("Priority", "P1", "F_prio", "O_p1")])
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
            [("F_status", "O_prog", "PI_1"), ("F_prio", "O_p1", "PI_1")],
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


class ManualOverrideTests(unittest.TestCase):
    """Manual board edits win over labels (compare-and-set)."""

    def run_sync(self, ev, gql, apply=True, config=CONFIG):
        out = []
        rc = ps.sync(ev, config, gql, apply, log=out.append)
        return rc, out

    def test_decide_matrix(self):
        cases = [
            ("In Progress", "In Progress", "Todo", ps.NOOP),
            ("In Progress", "in progress", None, ps.NOOP),
            ("In Progress", None, None, ps.SET),
            ("In Progress", "Todo", "Todo", ps.SET),
            ("In Progress", "Blocked", "Todo", ps.MANUAL),
            ("In Progress", "Todo", None, ps.MANUAL),
        ]
        for desired, current, previous, want in cases:
            with self.subTest(current=current, previous=previous):
                self.assertEqual(ps.decide(desired, current, previous), want)

    def test_label_change_updates_board_that_still_shows_old_label_value(self):
        gql = FakeGraphQL(items=[board_item(Status="Todo")])
        ev = event(
            labels=["status: in-progress"],
            action="labeled",
            label="status: in-progress",
        )
        self.run_sync(ev, gql)
        self.assertEqual([v["option"] for v in gql.sets()], ["O_prog"])

    def test_manual_status_wins_over_label(self):
        gql = FakeGraphQL(items=[board_item(Status="Blocked")])
        ev = event(
            labels=["status: in-progress"],
            action="labeled",
            label="status: in-progress",
        )
        rc, out = self.run_sync(ev, gql)
        self.assertEqual(rc, 0)
        self.assertEqual(gql.sets(), [])
        self.assertTrue(any("manual value wins" in line for line in out))

    def test_manual_priority_wins_after_unlabel(self):
        # Board P1 was set by hand; labels said P2 before and still say P2.
        gql = FakeGraphQL(items=[board_item(Priority="P1")])
        ev = event(
            labels=["priority: medium"], action="unlabeled", label="status: blocked"
        )
        self.run_sync(ev, gql)
        self.assertNotIn("F_prio", [v["field"] for v in gql.sets()])

    def test_unknown_previous_never_overwrites_existing_value(self):
        # "opened"/"edited": no provable "before", so only empty fields are filled.
        gql = FakeGraphQL(items=[board_item(Status="In Review")])
        self.run_sync(event(labels=["priority: high"], action="opened"), gql)
        self.assertEqual([v["field"] for v in gql.sets()], ["F_prio"])

    def test_close_moves_untouched_board_to_done_but_not_manual_value(self):
        gql = FakeGraphQL(items=[board_item(Status="In Progress")])
        self.run_sync(
            event(state="closed", labels=["status: in-progress"], action="closed"), gql
        )
        self.assertEqual([v["option"] for v in gql.sets()], ["O_done"])
        gql = FakeGraphQL(items=[board_item(Status="Blocked")])
        self.run_sync(
            event(state="closed", labels=["status: in-progress"], action="closed"), gql
        )
        self.assertEqual(gql.sets(), [])

    def test_already_matching_value_is_not_rewritten(self):
        gql = FakeGraphQL(items=[board_item(Status="In Progress")])
        ev = event(
            labels=["status: in-progress"],
            action="labeled",
            label="status: in-progress",
        )
        rc, out = self.run_sync(ev, gql)
        self.assertEqual(rc, 0)
        self.assertEqual(gql.sets(), [])
        self.assertIn("Status already In Progress", out)

    def test_dry_run_reports_manual_conflict_without_writing(self):
        gql = FakeGraphQL(items=[board_item(Status="Blocked")])
        ev = event(
            labels=["status: in-progress"],
            action="labeled",
            label="status: in-progress",
        )
        rc, out = self.run_sync(ev, gql, apply=False)
        self.assertEqual(rc, 0)
        self.assertEqual(gql.sets(), [])
        self.assertTrue(any("manual value wins" in line for line in out))
        self.assertFalse(any(line.startswith("dry run: would set") for line in out))

    def test_queue_is_never_written_even_if_configured(self):
        board = [
            *BOARD,
            {"id": "F_q", "name": "Queue", "options": [{"id": "O_q", "name": "x"}]},
        ]
        config = json.loads(json.dumps(CONFIG))
        config["fields"]["Queue"] = {"rules": [], "default": "x"}

        class Fake(FakeGraphQL):
            def __call__(self, query, variables):
                if "projectV2(number" in query:
                    self.calls.append((query, dict(variables)))
                    return {
                        "user": {"projectV2": {"id": "P_1", "fields": {"nodes": board}}}
                    }
                return super().__call__(query, variables)

        gql = Fake(items=[board_item()])
        self.run_sync(event(action="opened"), gql, config=config)
        self.assertNotIn("F_q", [v["field"] for v in gql.sets()])
        self.assertIn("Queue", CONFIG["protected_fields"])

    def test_no_write_no_item_added(self):
        gql = FakeGraphQL(items=[board_item(Status="Blocked")])
        self.run_sync(event(action="edited"), gql)
        self.assertFalse(any("addProjectV2ItemById" in q for q, _ in gql.calls))

    def test_previous_subject(self):
        labels = ["a", "B"]
        self.assertEqual(
            ps.previous_subject(
                {"action": "labeled", "label": {"name": "b"}}, "open", False, labels
            ),
            ("open", False, ["a"]),
        )
        self.assertEqual(
            ps.previous_subject(
                {"action": "unlabeled", "label": {"name": "c"}}, "open", False, labels
            ),
            ("open", False, ["a", "B", "c"]),
        )
        self.assertEqual(
            ps.previous_subject({"action": "reopened"}, "open", False, labels),
            ("closed", False, labels),
        )
        self.assertIsNone(
            ps.previous_subject({"action": "opened"}, "open", False, labels)
        )


class WorkflowContractTests(unittest.TestCase):
    def test_config_targets_active_project_and_protects_queue(self):
        self.assertEqual(CONFIG["project"], {"owner": "reihlinstinct", "number": 6})
        self.assertEqual(CONFIG["protected_fields"], ["Queue"])
        self.assertNotIn("Queue", CONFIG["fields"])

    def test_workflow_is_least_privilege_and_keeps_secrets_in_env(self):
        text = (ROOT / ".github/workflows/project-sync.yml").read_text(encoding="utf-8")
        self.assertIn("permissions: {}", text)
        self.assertNotIn("pull_request_target", text)
        run_lines = [ln for ln in text.splitlines() if ln.strip().startswith("run:")]
        self.assertEqual(run_lines, ["        run: python scripts/project_sync.py"])
        self.assertIn("PROJECT_PAT: ${{ secrets.PROJECT_PAT }}", text)


if __name__ == "__main__":
    unittest.main()
