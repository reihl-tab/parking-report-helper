# Offline monthly municipal draft

Run on a local checkout of the PRIVATE ledger. The standard-library-only module
reads `ledger/reports.csv` and prints a Hebrew Markdown draft. It makes no network
requests, changes no files, verifies no case mappings and sends nothing.

```sh
umask 077
python -m parking_report.monthly /private/parking-reports/ledger/reports.csv \
  --month 2026-10 > /private/monthly-2026-10.md
```

Do not run with real data in the public repository's Actions. Stdout contains
private references, dates and streets. Keep output private, including artifacts.
Recipients and final wording must be chosen and approved by the owner before
sending. This module does not install a schedule or email workflow.

## Data and counting

Required CSV columns: `folder_id`, `filed_date` (YYYY-MM-DD), `street`,
`event_type`, `municipal_ref`, `ref_mapping`. Extra fields, including plates,
vehicle data, house numbers, URLs and notes, are not included in the draft.
Read UTF-8 (a BOM is accepted). Select the filing month explicitly, not photo
month. Rows are report events, not unique vehicles, verified cases or enforcement
outcomes. Street/event counts describe the private ledger only. Do not deduce
municipal details from a provisional mapping.

Only exact `ref_mapping=verified` with a nonempty reference is presented as
verified. Every other status, including `unverified_fifo`, is visibly unverified.
An absent reference is marked missing. Email arrival/submission order never
verifies a car-to-case association. This generator cannot change mapping status.

The complete reference is retained as text, including leading zeros and hyphens.
Current October 2026 email references have a `2610-` prefix followed by a running
number. Preserve the full observed value; do not generate references, infer a
street, or assume future formats from this pattern. Synthetic tests use
`2610-000001`, not a real municipal reference.

Counts sort descending, ties sort by street/type; report rows sort by filing date
and stable folder ID. Invalid dates/months, missing required columns, duplicate
report IDs, duplicate nonempty references and control characters block the whole
draft with a generic error. No partial stdout or private error values are printed.
Table text is escaped. Unknown mapping values are not promoted to verified.

Street and event counts use the values with surrounding whitespace removed,
while the reference table shows each value exactly as recorded in the ledger.
This difference is kept on purpose and pinned by tests (#133).

## Verification

Unit tests cover month selection, counts, deterministic ordering, unknown/missing
mappings, duplicate guards, escaping and omitted private fields. Functional tests
run the actual module CLI on temporary synthetic CSVs, including BOM and failure
cases. Existing submission and social workflow gates are unchanged.
