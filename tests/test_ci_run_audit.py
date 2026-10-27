"""Synthetic tests for the offline CI run audit (#124). No network."""

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "ci_run_audit", ROOT / "scripts/ci_run_audit.py"
)
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


def run(event, sha, branch="feature/x"):
    return {"event": event, "head_sha": sha, "head_branch": branch}


class AuditTests(unittest.TestCase):
    def test_push_and_pull_request_on_same_sha_is_one_extra_run(self):
        runs = [run("push", "a" * 40), run("pull_request", "a" * 40)]
        s = a.audit(runs)
        self.assertEqual(s["commits_run_by_both_push_and_pull_request"], 1)
        self.assertEqual(s["extra_runs"], 1)
        self.assertEqual(s["duplicated_shas"], ["aaaaaaa"])

    def test_main_push_alone_is_not_a_duplicate(self):
        runs = [run("push", "b" * 40, "main"), run("pull_request", "c" * 40)]
        s = a.audit(runs)
        self.assertEqual(s["extra_runs"], 0)
        self.assertEqual(s["push_on_default_branch"], 1)
        self.assertEqual(s["push_on_other_branches"], 0)

    def test_two_runs_of_one_event_are_not_counted_as_trigger_overlap(self):
        # A manual re-run or two pushes of one SHA is not push/PR overlap.
        runs = [run("push", "d" * 40), run("push", "d" * 40)]
        self.assertEqual(a.audit(runs)["extra_runs"], 0)

    def test_repeated_pull_request_runs_are_not_extra(self):
        # Review r4392397070: a second pull_request run of one SHA (reopen,
        # base change) is a same-event repeat; push-to-main-only removes only
        # the push run, so the saving is 1.
        runs = [
            run("push", "e" * 40),
            run("pull_request", "e" * 40),
            run("pull_request", "e" * 40),
        ]
        self.assertEqual(a.audit(runs)["extra_runs"], 1)

    def test_every_push_run_of_an_overlapping_commit_is_extra(self):
        runs = [
            run("push", "6" * 40),
            run("push", "6" * 40),
            run("pull_request", "6" * 40),
        ]
        self.assertEqual(a.audit(runs)["extra_runs"], 2)

    def test_jobs_per_run_must_be_positive(self):
        for bad in ("0", "-7", "x"):
            with (
                self.subTest(bad=bad),
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                a.main(["unused.json", "--jobs-per-run", bad])

    def test_other_events_do_not_create_overlap(self):
        runs = [run("push", "f" * 40), run("workflow_dispatch", "f" * 40)]
        s = a.audit(runs)
        self.assertEqual(s["extra_runs"], 0)
        self.assertEqual(s["events"], {"push": 1, "workflow_dispatch": 1})

    def test_default_branch_is_configurable(self):
        runs = [run("push", "1" * 40, "master")]
        s = a.audit(runs, default_branch="master")
        self.assertEqual(s["push_on_default_branch"], 1)
        self.assertEqual(s["push_on_other_branches"], 0)

    def test_accepts_api_response_object_and_bare_list(self):
        runs = [run("push", "2" * 40)]
        self.assertEqual(a.load_runs({"total_count": 1, "workflow_runs": runs}), runs)
        self.assertEqual(a.load_runs(runs), runs)

    def test_malformed_input_is_rejected(self):
        for bad in (
            {"message": "Not Found"},
            "text",
            [None],
            [{"event": "push", "head_branch": "x"}],
            [{"event": "", "head_sha": "3" * 40, "head_branch": "x"}],
        ):
            with self.subTest(bad=bad), self.assertRaises(a.AuditError):
                a.load_runs(bad)

    def test_render_reports_counts_and_short_shas_only(self):
        runs = [
            run("push", "4" * 40, "secret-looking-branch"),
            run("pull_request", "4" * 40, "secret-looking-branch"),
        ]
        text = a.render(a.audit(runs), jobs_per_run=7)
        self.assertIn("extra runs: 1", text)
        self.assertIn("extra jobs (at 7 per run): 7", text)
        self.assertIn("duplicated commits: 4444444", text)
        self.assertNotIn("secret-looking-branch", text)
        self.assertNotIn("4" * 40, text)

    def test_main_reads_file_and_fails_cleanly_on_bad_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "runs.json"
            good.write_text(json.dumps({"workflow_runs": [run("push", "5" * 40)]}))
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(a.main([str(good)]), 0)
            self.assertIn("runs: 1", out.getvalue())

            bad = Path(tmp) / "bad.json"
            bad.write_text("not json")
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(a.main([str(bad)]), 1)
            self.assertIn("ci_run_audit:", err.getvalue())
            with redirect_stderr(io.StringIO()):
                self.assertEqual(a.main([str(Path(tmp) / "missing.json")]), 1)


if __name__ == "__main__":
    unittest.main()
