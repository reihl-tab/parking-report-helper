# Historical regression audit, batch 1

Target/checkpoint: 22 October 2026. Issue #78. All fixtures are synthetic.

| Historical defect | Coverage before audit | Batch result |
| --- | --- | --- |
| #62 / `1418f57`, malformed quadruple Actions-expression braces | No dedicated test | New guard scans current workflows, rejects original malformed name and unclosed expression; coverage job retains its static name |
| `bb6b985` / #45, unknown/unmatched registry result stated as a finding | `test_registry.DescribeTests.test_unknown_is_omitted` | Already covered |
| `06bdecd` / #42, registry prose and coordinates | `test_registry` single registry sentence and `test_intake` no coordinates | Already covered; added direct report-text coordinate guard |

Three historical defects reviewed. Seven new test methods, including four extra
registry checks: an old last-test date with a still-valid licence, a recent test
with expired validity, missing validity, and the exact expiry-date boundary.
`mivchan_acharon_dt` is a last-test date; only `tokef_dt` determines licence expiry.
These strengthen existing coverage rather than count as additional historical
bugs. No production helper code changes were needed.

The workflow test is an exact regression guard, not a full GitHub Actions parser.
Hosted CI still validates the complete workflow. Docker builds and all hosted
matrix jobs must finish before merge. No private reports, identifiers or sources
were copied into this public repository.
