# CI efficiency audit

Issue #124. This is a measure-only document. This PR changes no workflow. Each proposal below becomes its own PR, with a workflow-contract test, after a maintainer decision.

All numbers come from checked-in workflows on `main` `3fa4519` and from public Actions run metadata. The figures were produced offline with `scripts/ci_run_audit.py` from saved run lists. Nothing was dispatched or re-run.

## Inventory

| Workflow | Triggers | Jobs per run | Notes |
| --- | --- | --- | --- |
| `tests.yml` | `push` (every branch), `pull_request`, `workflow_dispatch` | 7 | 3 Python versions (3.11, 3.12, 3.13); coverage (3.12); mypy (3.12); Docker; security (bandit and pip-audit, 3.12) |
| `gitleaks.yml` | `push` (every branch), `pull_request`, `workflow_dispatch` | 1 | secret scanning |
| `codeql.yml` | `push` to `main`, `pull_request` to `main`, weekly | 2 | no overlap on PR branches |
| `dependency-review.yml` | `pull_request` | 1 | |
| `scorecard.yml` | `push` to `main`, weekly | 1 | |
| `add-to-project.yml`, `project-sync.yml` | `issues` | 1 | issue events only (#119) |
| `api106.yml` | `workflow_call` | — | private runner, not run by this repo's CI |
| `manual-gpg-signing.yml` | `workflow_dispatch` | — | manual only |

Every `tests.yml` job has a `timeout-minutes` (5 or 10). No workflow uses `concurrency` to cancel superseded PR runs, and no `setup-python` step caches pip.

## Measurements

**Sample:** the last 40 runs of each workflow, from 2026-10-26 17:13 to 2026-10-27 13:51 UTC.

```
$ scripts/ci_run_audit.py tests-runs.json --jobs-per-run 7
runs: 40
commits: 25
events: pull_request=18, push=22
push on default branch: 7
push on other branches: 15
commits run by both push and pull_request: 15
extra runs: 15
extra jobs (at 7 per run): 105
```

`gitleaks.yml` gives the same picture over the same window: 15 extra runs of 1 job each.

So **15 of 40 Tests runs (37.5 %) repeated a run of the same commit.** Each pushed commit on a same-repo PR branch runs Tests twice:
- once for `push`, on the branch head;
- once for `pull_request`, on the merge ref.

**Job durations** come from one push run (`40510790148`, commit `1e3ac16`):

| Job | Duration |
| --- | --- |
| Docker | 48 s |
| Python 3.11 | 21 s |
| Python 3.12 | 21 s |
| Python 3.13 | 21 s |
| Coverage | 25 s |
| mypy | 15 s |
| Security | 16 s |
| **Total** | **≈ 167 runner-seconds (≈ 50 s wall time)** |

The 15 duplicate runs cost about 42 runner-minutes over the window. Rounded per job, the way Actions minutes are counted, that is 105 job-minutes.

This is a public repository, and standard GitHub-hosted runners are not billed for public repositories. The saving is runner capacity, queue time, and fewer duplicate checks on each commit, not money.

## Proposals, ranked

### 1. Run `tests.yml` on `push` to `main` only (recommended)

The change:

```yaml
on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:
```

- **Removes:** every duplicate Tests run, which is 15 of 40 in the sample.
- **Unchanged:**
  - all 7 jobs and the Python 3.11–3.13 matrix;
  - Docker, coverage, mypy and security;
  - artifacts and their retention;
  - every gate variable (`COVERAGE_GATE`, `MYPY_GATE`, `SECURITY_GATE`).

  PRs keep the `pull_request` run, which tests the merge ref. `main` keeps a run on every push.
- **Trade-off:** a branch pushed without an open PR gets no Tests run until a PR opens. `workflow_dispatch` can still run one by hand.
- **Required checks:** none. Instinct verified on 2026-10-27 (review request 6444556068 on #130) that branch protection on `main` has no required status checks, so this change cannot break a required check. If required checks are added later, they should come from the `pull_request` run; the check names stay the same.
- **Contract test for that PR:** `push` is limited to `main`; `pull_request` has no branch filter; the job names and the matrix stay unchanged.

### 2. Cancel superseded PR runs (optional)

Add `concurrency: {group: tests-${{ github.event.pull_request.number || github.sha }}, cancel-in-progress: ${{ github.event_name == 'pull_request' }}}` to `tests.yml`.

- **Effect:** quick re-pushes no longer finish runs for heads nobody will merge. In the sample, `d8d1425` and `62ec621` were pushed 24 s apart.
- **`main` is unaffected:** its runs never cancel, because the group falls back to the SHA and cancelling applies only to `pull_request` events.
- **Trade-off:** the old head shows a cancelled check.

### 3. Leave `gitleaks.yml` as is (recommended)

Gitleaks also runs twice per PR-branch commit. But secret scanning on every push to any branch is a safety gate: a secret pushed to a public branch that has no PR yet must still be caught. Each duplicate is a single job of about 10 s. Not proposed.

### Not proposed

- **Folding coverage into the Python 3.12 job.** It would save 1 job per run, but it would remove or rename the `Coverage report` check. No check is required today (verified 2026-10-27), so nothing breaks, but the saving is 1 short job per run and the separate `Coverage report` check is easier to read. Not worth the change now.
- **pip caching.** Installs take 4–10 s per job, which is about what a cache restore costs, so caching gains nothing measurable.
- **Dropping the tests inside the Docker build (`--target test`).** That step is the container check: it verifies the image's own environment. Keep it.

## Reproducing

Save a run list with any authenticated read of the public API, then run the script offline:

```
gh api 'repos/reihlinstinct/parking-report-helper/actions/workflows/tests.yml/runs?per_page=40' > tests-runs.json
scripts/ci_run_audit.py tests-runs.json --jobs-per-run 7
```

The script reads only the saved file. Its output contains only counts and 7-character commit SHAs: no branch names, no titles.
