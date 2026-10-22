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

## Nested assertion detail

Each test has its own collapsed section. The static source plan lists call names,
relative lines and argument AST shapes; literal input values are never copied.
This is explicitly a plan, not proof every branch ran. Runtime wrapping of this
test instance's unittest assertion methods records the observed method, inputs,
expected/actual for supported methods, and whether the call returned or raised.
Original methods are restored afterward. Internal nested assertions are deduplicated.

Strings, arbitrary objects, containers and large numbers are withheld without
calling repr/str on them. Only None, bool and small finite numeric values appear.
Context-manager assertions report that the context was created; the final test
status is the verdict. Plain Python assert, assertions hidden in external helpers,
mock-call assertions and setup failure may have no observed details. Missing data
is shown as not captured, not fabricated. The report is therefore a useful
privacy-limited execution view, not a complete trace of every variable or branch.

Skipped tests include a plain-language explanation and an alternate job only
where the route is verified: workflow-source tests excluded by Docker run in full
checkout Python/coverage jobs; missing API extras are installed by those jobs.
Unknown skip reasons explicitly say no alternate execution route was verified.
No browser/Playwright job is invented. Five added tests cover observation,
restoration, privacy, static plans and skip-route explanations.
