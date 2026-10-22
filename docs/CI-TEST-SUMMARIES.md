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

## Per-test detail follow-up

A collapsible list now shows each source test ID, its docstring (or method name),
status and safe reason. Failures/errors/unexpected successes and skips appear
first. Failure type is shown, assertion bodies/tracebacks remain in existing job
logs. Skip reasons are included with email/long-number/path/credential-pattern
redaction. HTML is escaped. Subtest parameter values are omitted. This is a
source-name explanation, not an invented natural-language claim about coverage.

The in-run metrics file contains sanitized details but is not uploaded to any
public artifact. This redaction is defense in depth, not proof arbitrary messages
contain no secrets: tests should use synthetic values and safe source names.
Normal job logs retain unittest's existing output and access controls. Six
synthetic test methods verify category accounting, failure propagation, safe
reasons, ordering and escaping. No live data is added to public summaries.
