# Design

## Purpose

Turn a JSON list of reported vehicles into text a person can send to a municipality:
either a Hebrew report draft, or the values of a municipality's web form. A person
always reviews. Browser mode has been removed. The optional API runner requires an
exact preview digest and explicit live enablement. The reusable workflow is
preview-only. See API106.md for the separate API boundary.

## Non-goals and safety invariants

These are tested and must hold for every change:

1. API preview is offline by default; live execution needs exact reviewed digest and explicit enablement.
2. Never solve, bypass or interact with a captcha or other bot check. Stop and report.
3. No browser navigation or form automation remains.
4. One request per diagnostic run, no retries on HTTP errors or verification walls.
5. No personal data in the repository. Reporter details live in a file outside it.
6. Core drafts remain dependency-free; API image/coordinate/locking dependencies are optional.
7. Intake is read-only: GET requests to the Drive API host only, no uploads or edits, no retries, no tokens in the repository. Records that are not `ready` are skipped, never guessed.

## Layers

```
cli.py            argument parsing, file I/O, exit codes, output printing
   |
municipality.py   config loading, address splitting, form field values   (pure, except load_config)
report.py         Car model, validation, Hebrew report text              (pure)

api106.py         optional API transport, state persistence and duplicate guards
api_cli.py        env-configured offline preview and explicit submission gate
registry.py       official exact-plate GETs, private output only
intake.py         read report folders (report.json + photo) -> cars   (read-only file I/O)
drive.py          optional read-only Drive download into a local folder (stdlib urllib)
configs/*.json    one data file per municipality
```

Dependencies point downward only. `report.py` imports nothing from the package.
`municipality.py` imports `report.py`. API dependencies are optional, so the core installs without them. `cli.py` is the only module that reads files,
prints, or sets exit codes.

## Data flow

```
input.json --> Car.from_mapping (validate) --> build_report            --> text
                                           \-> build_submissions(config, reporter)
                                                  |-> format_submission / submission_dict --> stdout
```

A submission is an ordered list of `(key, label, value)` rows, one per config field.
Required values that are not available become `MISSING` (`<חסר>`) so gaps are visible
instead of invented. Browser code skips those fields.

Intake (`--intake`, see `INTAKE.md`) replaces the JSON file as the source of cars:
`Drive folder --(drive.py)--> local folder --(intake.py)--> cars + photos --> same pipeline`.

## Error handling

Input and usage problems raise `ValueError`.
`cli.main` turns them into `error: ...` on stderr and exit code 1, without tracebacks and
without partial stdout. Invalid records name their 1-based position (`car 2: ...`).
Option combinations that make no sense (for example `--reporter` without `--municipality`)
are rejected by argparse with exit code 2 instead of being silently ignored.

## Extending

A new municipality is one JSON file in `configs/` (see the README). No code change is
needed unless the form has widgets that labels or selectors cannot reach.

## Test strategy

Unit and functional CLI tests cover drafts, intake, API validation and uncertain
creation/duplicate guards. Registry tests use mocked government responses. Generated
photos and synthetic identities replace real data. CI runs Python 3.11-3.13 and
Docker builds. Browser form/mock tests and live probes have been removed.
No municipality requests occur in tests. docs/API106.md owns API/state semantics.

## Municipal issue selection

`subjects.py` loads a packaged, validated snapshot of municipal subject codes.
Each report selects a subject in its own `municipal_subject` field. Intake uses
the selected subject to choose vehicle or general-issue draft semantics; it
never infers a subject from the photo or relabels the true event. Non-vehicle
issues need no plate. Explicit routing records the owner choice and matching
event type, and requires exact approved text before any live transport. The
digest binds these fields as well as the existing payload and evidence.
Missing selection preserves 1145 and the sidewalk-only live default. The legacy
1145 flag is restricted to historical records. See SUBJECTS.md.
