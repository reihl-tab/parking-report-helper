# Changelog

Flag, default, exit-code and JSON-key changes are recorded here, as required by the
[flag-stability policy](docs/CLI-CONTRACT.md#flag-stability-policy).

## Unreleased

- `parking-report-106`: after a successful submit, re-list today's cases and detect a server-side duplicate of the just-submitted text (#160). Duplicates exit with the new code 5 and print `duplicate_check: "duplicates_found"` plus `duplicate_case_numbers`; clean submissions print `duplicate_check: "single"` or `"pending"` and a failed check prints `"unavailable"` without failing the submission. New `api106.duplicate_case_numbers` helper. Minor version bump per the flag-stability policy.

- Adopted the CLI flag-stability policy (#73). No flag, default or exit code changed.
