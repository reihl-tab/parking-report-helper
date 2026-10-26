"""Label -> project-board proposals. Synthetic events and a fake GraphQL
transport; no network, no token. The sync is proposal only: no test may see a
board write."""

import importlib.util
import json
import re
import unittest
from pathlib import Path
from unittest import mock

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
        if re.search(r"\bmutation\b", query, re.IGNORECASE):
            raise AssertionError("board write attempted: " + query)
        if "projectItems" in query:
            return {"node": {"projectItems": {"nodes": self.items}}}
        raise AssertionError(query)


def proposals(out):
    """[(field, option)] from the log lines "proposal: set F = O"."""
    found = []
    for line in out:
        m = re.fullmatch(r"proposal: set (.+) = (.+)", line)
        if m:
            found.append((m.group(1), m.group(2)))
    return found


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
    def run_sync(self, ev, gql, config=CONFIG):
        out = []
        rc = ps.sync(ev, config, gql, log=out.append)
        return rc, out

    def test_reads_board_and_proposes_never_writes(self):
        gql = FakeGraphQL(items=[{"id": "PI_1", "project": {"id": "P_1"}}])
        rc, out = self.run_sync(
            event(labels=["status: in-progress", "priority: high"]), gql
        )
        self.assertEqual(rc, 0)
        self.assertEqual(proposals(out), [("Status", "In Progress"), ("Priority", "P1")])
        self.assertIn("proposal only: nothing was written to the board", out)
        self.assertEqual(len(gql.calls), 2)  # project fields + item values, reads only

    def test_missing_item_is_proposed_not_added(self):
        gql = FakeGraphQL(items=[{"id": "PI_other", "project": {"id": "P_other"}}])
        _, out = self.run_sync(event(), gql)
        self.assertIn("proposal: add the item to the project", out)
        self.assertEqual(proposals(out), [("Status", "Todo")])

    def test_closed_issue_proposes_done(self):
        gql = FakeGraphQL(items=[{"id": "PI_1", "project": {"id": "P_1"}}])
        _, out = self.run_sync(event(state="closed"), gql)
        self.assertEqual(proposals(out), [("Status", "Done")])

    def test_missing_board_option_is_skipped_with_warning(self):
        gql = FakeGraphQL(items=[{"id": "PI_1", "project": {"id": "P_1"}}])
        rc, out = self.run_sync(event(labels=["status: blocked"]), gql)
        self.assertEqual(rc, 0)
        self.assertEqual(proposals(out), [])
        self.assertTrue(any(line.startswith("::warning::") for line in out))

    def test_without_token_reads_nothing(self):
        rc, out = self.run_sync(event(), None)
        self.assertEqual(rc, 0)
        self.assertIn("no PROJECT_PAT available: board not read, labels only", out)

    def test_event_without_subject_is_a_noop(self):
        rc, _ = self.run_sync({"action": "x"}, FakeGraphQL())
        self.assertEqual(rc, 0)


class ReadOnlyTests(unittest.TestCase):
    """The sync has no write path, and its client refuses one."""

    def test_no_mutation_in_module(self):
        source = (ROOT / "scripts/project_sync.py").read_text(encoding="utf-8")
        for name in ("addProjectV2ItemById", "updateProjectV2ItemFieldValue",
                     "clearProjectV2ItemFieldValue", "deleteProjectV2Item"):
            self.assertNotIn(name, source)
        for value in vars(ps).values():
            if isinstance(value, str):
                self.assertIsNone(re.match(r"\s*mutation\b", value, re.IGNORECASE))

    def test_client_refuses_mutation_before_network(self):
        call = ps.graphql_client("synthetic-token")
        with mock.patch.object(ps.urllib.request, "urlopen") as urlopen:
            for query in ("mutation { x }", "  Mutation($a: ID!) { x }",
                          "query { a } mutation { b }"):
                with self.subTest(query=query), self.assertRaises(ValueError):
                    call(query, {})
            urlopen.assert_not_called()

    def test_client_still_sends_queries(self):
        call = ps.graphql_client("synthetic-token")
        response = mock.MagicMock()
        response.__enter__.return_value = response
        with mock.patch.object(ps.urllib.request, "urlopen", return_value=response), \
                mock.patch.object(ps.json, "load", return_value={"data": {"ok": 1}}):
            self.assertEqual(call(ps.Q_PROJECT, {"login": "x", "number": 1}), {"ok": 1})

    def test_main_failure_never_echoes_exception_text(self):
        out = []
        with mock.patch.object(ps, "sync", side_effect=RuntimeError("synthetic-secret")), \
                mock.patch.dict(ps.os.environ, {"GITHUB_EVENT_PATH": str(ROOT / "x"),
                                                "PROJECT_PAT": ""}), \
                mock.patch.object(ps.Path, "read_text", return_value="{}"), \
                mock.patch("builtins.print", side_effect=out.append):
            self.assertEqual(ps.main(), 0)
        self.assertEqual(out, ["::warning::project sync proposal failed: RuntimeError"])


class ManualOverrideTests(unittest.TestCase):
    """Manual board edits win over labels (compare-and-set)."""

    def run_sync(self, ev, gql, config=CONFIG):
        out = []
        rc = ps.sync(ev, config, gql, log=out.append)
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

    def test_label_change_proposed_when_board_still_shows_old_label_value(self):
        gql = FakeGraphQL(items=[board_item(Status="Todo")])
        ev = event(
            labels=["status: in-progress"],
            action="labeled",
            label="status: in-progress",
        )
        _, out = self.run_sync(ev, gql)
        self.assertEqual(proposals(out), [("Status", "In Progress")])

    def test_manual_status_wins_over_label(self):
        gql = FakeGraphQL(items=[board_item(Status="Blocked")])
        ev = event(
            labels=["status: in-progress"],
            action="labeled",
            label="status: in-progress",
        )
        rc, out = self.run_sync(ev, gql)
        self.assertEqual(rc, 0)
        self.assertEqual(proposals(out), [])
        self.assertTrue(any("manual value wins" in line for line in out))

    def test_manual_priority_wins_after_unlabel(self):
        # Board P1 was set by hand; labels said P2 before and still say P2.
        gql = FakeGraphQL(items=[board_item(Priority="P1")])
        ev = event(
            labels=["priority: medium"], action="unlabeled", label="status: blocked"
        )
        _, out = self.run_sync(ev, gql)
        self.assertNotIn("Priority", [f for f, _ in proposals(out)])

    def test_unknown_previous_never_overwrites_existing_value(self):
        # "opened"/"edited": no provable "before", so only empty fields are filled.
        gql = FakeGraphQL(items=[board_item(Status="In Review")])
        _, out = self.run_sync(event(labels=["priority: high"], action="opened"), gql)
        self.assertEqual(proposals(out), [("Priority", "P1")])

    def test_close_proposes_done_for_untouched_board_but_not_manual_value(self):
        gql = FakeGraphQL(items=[board_item(Status="In Progress")])
        _, out = self.run_sync(
            event(state="closed", labels=["status: in-progress"], action="closed"), gql
        )
        self.assertEqual(proposals(out), [("Status", "Done")])
        gql = FakeGraphQL(items=[board_item(Status="Blocked")])
        _, out = self.run_sync(
            event(state="closed", labels=["status: in-progress"], action="closed"), gql
        )
        self.assertEqual(proposals(out), [])

    def test_already_matching_value_is_not_proposed(self):
        gql = FakeGraphQL(items=[board_item(Status="In Progress")])
        ev = event(
            labels=["status: in-progress"],
            action="labeled",
            label="status: in-progress",
        )
        rc, out = self.run_sync(ev, gql)
        self.assertEqual(rc, 0)
        self.assertEqual(proposals(out), [])
        self.assertIn("Status already In Progress", out)

    def test_queue_is_never_proposed_even_if_configured(self):
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
        _, out = self.run_sync(event(action="opened"), gql, config=config)
        self.assertNotIn("Queue", [f for f, _ in proposals(out)])
        self.assertIn("Queue", CONFIG["protected_fields"])

    def test_no_proposal_no_item_add_proposed(self):
        gql = FakeGraphQL(items=[board_item(Status="Blocked")])
        _, out = self.run_sync(event(action="edited"), gql)
        self.assertNotIn("proposal: add the item to the project", out)

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
        run_lines = [ln for ln in text.splitlines() if ln.strip().startswith("run:")]
        self.assertEqual(run_lines, ["        run: python scripts/project_sync.py"])
        self.assertIn("PROJECT_PAT: ${{ secrets.PROJECT_PAT }}", text)
        self.assertNotIn("PROJECT_SYNC", text)


PR_TRIGGERS = ("pull_request", "pull_request_target", "workflow_run")


def workflow_triggers(text):
    """Top-level event names under `on:` (block form, as this repo writes it)."""
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if re.fullmatch(r"on:\s*", ln))
    events = []
    for ln in lines[start + 1:]:
        if ln and not ln[0].isspace() and not ln.startswith("#"):
            break
        m = re.fullmatch(r"  ([A-Za-z_]+):.*", ln)
        if m:
            events.append(m.group(1))
    return events


class TokenTrustBoundaryTests(unittest.TestCase):
    """Regression for the PR-head secret exposure: no pull-request-controlled
    code may run in a job that receives PROJECT_PAT."""

    def setUp(self):
        self.text = (ROOT / ".github/workflows/project-sync.yml").read_text(
            encoding="utf-8"
        )

    def test_trigger_parser(self):
        sample = "name: x\non:\n  issues:\n    types: [opened]\n  pull_request:\n\njobs:\n"
        self.assertEqual(workflow_triggers(sample), ["issues", "pull_request"])

    def test_sync_runs_only_on_issue_events(self):
        self.assertEqual(workflow_triggers(self.text), ["issues"])

    def test_sync_checks_out_default_branch_not_pr_head(self):
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", self.text)
        self.assertNotIn("github.event.pull_request", self.text)
        self.assertNotIn("head_ref", self.text)
        self.assertNotIn("head.sha", self.text)

    def test_no_pr_triggered_workflow_runs_code_with_project_token(self):
        # A pull_request-triggered job that checks out or runs code gets
        # PR-controlled code; it must never see PROJECT_PAT.
        for path in sorted((ROOT / ".github/workflows").glob("*.y*ml")):
            text = path.read_text(encoding="utf-8")
            if "secrets.PROJECT_PAT" not in text:
                continue
            if not set(workflow_triggers(text)) & set(PR_TRIGGERS):
                continue
            with self.subTest(workflow=path.name):
                self.assertNotIn("actions/checkout", text)
                self.assertNotRegex(text, r"(?m)^\s*(- )?run:")


if __name__ == "__main__":
    unittest.main()
