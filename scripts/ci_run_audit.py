#!/usr/bin/env python3
"""Count duplicate GitHub Actions runs from saved public run metadata (#124).

Offline and read-only: the input is a JSON file someone already saved from
``GET /repos/{owner}/{repo}/actions/workflows/{workflow}/runs`` (the response
object, or just its ``workflow_runs`` list). This script never calls the
network, never dispatches or re-runs anything, and prints only counts and
short commit SHAs.

A *duplicate* is a ``push`` run for a commit that also ran for
``pull_request``: the same ``head_sha`` ran for both triggers. ``extra runs``
counts those push runs, which is what limiting ``push`` to the default branch
would remove; repeated runs of one event are not counted. That happens when a workflow triggers on ``push`` to every
branch as well as on ``pull_request`` and the PR branch lives in the same
repository.

Usage:
    scripts/ci_run_audit.py RUNS.json [--default-branch main] [--jobs-per-run 7]
"""

import argparse
import json
import sys
from collections import Counter, defaultdict

PAIR = frozenset({"push", "pull_request"})


class AuditError(Exception):
    """The input is not usable run metadata."""


def load_runs(data):
    """Return the list of run objects from a runs response or a bare list."""
    if isinstance(data, dict):
        data = data.get("workflow_runs")
    if not isinstance(data, list):
        raise AuditError("expected a list of workflow runs")
    for run in data:
        if not isinstance(run, dict):
            raise AuditError("malformed run entry")
        for key in ("event", "head_sha", "head_branch"):
            if not isinstance(run.get(key), str) or not run[key]:
                raise AuditError(f"run entry without a usable {key!r}")
    return data


def audit(runs, default_branch="main"):
    """Summarise trigger overlap. Returns a plain dict of counts."""
    events = Counter(run["event"] for run in runs)
    by_sha = defaultdict(list)
    for run in runs:
        by_sha[run["head_sha"]].append(run)
    duplicated = []
    extra_runs = 0
    for sha, group in by_sha.items():
        kinds = {run["event"] for run in group}
        if not PAIR <= kinds:
            continue
        # The push runs of a commit that also ran for pull_request are the
        # runs a push-to-default-branch-only trigger would remove. Repeated
        # pull_request runs of the same SHA are not trigger overlap.
        extra_runs += sum(run["event"] == "push" for run in group)
        duplicated.append(sha)
    pr_branch_pushes = sum(
        1
        for run in runs
        if run["event"] == "push" and run["head_branch"] != default_branch
    )
    return {
        "runs": len(runs),
        "commits": len(by_sha),
        "events": dict(sorted(events.items())),
        "push_on_default_branch": events.get("push", 0) - pr_branch_pushes,
        "push_on_other_branches": pr_branch_pushes,
        "commits_run_by_both_push_and_pull_request": len(duplicated),
        "extra_runs": extra_runs,
        "duplicated_shas": sorted(sha[:7] for sha in duplicated),
    }


def render(summary, jobs_per_run=None):
    lines = [
        f"runs: {summary['runs']}",
        f"commits: {summary['commits']}",
        "events: " + ", ".join(f"{k}={v}" for k, v in summary["events"].items()),
        f"push on default branch: {summary['push_on_default_branch']}",
        f"push on other branches: {summary['push_on_other_branches']}",
        (
            "commits run by both push and pull_request: "
            f"{summary['commits_run_by_both_push_and_pull_request']}"
        ),
        f"extra runs: {summary['extra_runs']}",
    ]
    if jobs_per_run:
        lines.append(
            f"extra jobs (at {jobs_per_run} per run): "
            f"{summary['extra_runs'] * jobs_per_run}"
        )
    lines.append("duplicated commits: " + " ".join(summary["duplicated_shas"]))
    return "\n".join(lines)


def _positive_int(text):
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("runs_json")
    ap.add_argument("--default-branch", default="main")
    ap.add_argument("--jobs-per-run", type=_positive_int, default=None)
    args = ap.parse_args(argv)
    try:
        with open(args.runs_json, encoding="utf-8") as fh:
            runs = load_runs(json.load(fh))
    except (OSError, ValueError, AuditError) as exc:
        print(f"ci_run_audit: {exc}", file=sys.stderr)
        return 1
    print(render(audit(runs, args.default_branch), args.jobs_per_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
