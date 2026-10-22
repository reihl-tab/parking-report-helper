# CI test-run summaries

Every Python test and coverage job writes aggregate counts to GitHub's job
summary: run, passed, failed, errored, skipped, expected failures and unexpected
successes. A failed unittest run still exits nonzero. Coverage jobs add the
coverage.py statement/branch percentage; other jobs say it was not measured.
Summary steps run with `always()` even when an earlier test/install step fails.
If metrics are missing, the summary says unavailable rather than claiming success.

The runner is `scripts/ci_test_report.py run --start tests --output test-results.json`.
The renderer is `scripts/ci_test_report.py summary --metrics test-results.json
--coverage coverage.json`. On GitHub it appends to `GITHUB_STEP_SUMMARY`; locally
it prints Markdown. It copies aggregate integers only, never test IDs, assertion
text, paths or private results. Existing job logs still show normal unittest output.

Synthetic functional tests cover passing/failing/import-error suites, skipped
and expected-failure accounting, preserved exit status, coverage parsing and
summary append behavior. Docker remains a build/smoke result, not an invented
unittest count. If a runner never starts (billing restriction), no in-job script
can produce a summary. Hosted job status remains the source of truth.
