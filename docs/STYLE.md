# Style guide

Working rules for this repository. This document grows as conventions are agreed (#67).

## Diagrams

Any issue or proposal that needs design - API, containers, architecture, data flow, security boundaries, test pipeline - includes a Mermaid diagram in the issue body or in `docs/`. Mermaid renders natively on GitHub, stays in version control, and adds no binary files. A diagram without a design question is decoration; a design without a diagram is incomplete.

Diagrams never contain real licence plates, exact addresses, GPS coordinates, or any living-person data. Use synthetic placeholders. This applies to issue text, `docs/`, and PR descriptions alike (owner rule, 2026-10-20).

## Commit and PR style

One issue per change, one PR per issue, full rationale in the commit body. Squash merge to `main`; CI must be green.

## Lint and format (#64)

`ruff` is the linter and formatter. The settings live in `[tool.ruff]` in
`pyproject.toml`: target Python 3.11, line length 88, and rules `E4`, `E7`,
`E9`, `F` (pyflakes) and `I` (import order). Before pushing, run:

```sh
pip install "ruff==0.16.9"
ruff check .
ruff format --check .   # or `ruff format .` to apply
```

CI runs both checks in the `Ruff (lint + format)` job with the same pinned
version. The job is report-only (a warning) until the repository variable
`RUFF_GATE=on` makes it blocking, the same pattern as `MYPY_GATE`. Widening the
rule set is its own PR with the fixes.

## Language

- Code, identifiers, comments, docs, commit messages and PR text are in **English**.
- Text that reaches a person (the municipal report, its review text, monthly drafts) is in **Hebrew**. It is built in the module that owns it (`report.py`, `registry.describe_he`), never assembled across modules.
- Functions that return Hebrew text carry a `_he` suffix (`describe_he`, `_he_date`).
- Dates in Hebrew text use `D.M.YYYY`, with times as `HH:MM` (`format_datetime`, `_he_date`). Data files and JSON keep ISO `YYYY-MM-DD`.
- Unknown is not a finding. If a lookup fails or matches nothing, the Hebrew text omits that fact; it never states the negative ("no valid licence").

## Naming and layout

- Modules and functions are `snake_case`; classes are `CapWords`; constants are `UPPER_CASE` (`REQUIRED`, `BADGES`).
- One module per responsibility under `src/parking_report/` (intake, registry, report, api106, ledger, monthly...). Entry points are listed in `pyproject.toml` `[project.scripts]`.
- Line length follows ruff's default (88). Long Hebrew strings may exceed it rather than be split mid-sentence.

## Lint, format and hooks

- `ruff check .` must pass (ruff 0.17). The formatting baseline (`ruff format`) is tracked in #64. Until it lands, do not reformat files you are not otherwise changing.
- `pre-commit install` once per clone runs the hooks in `.pre-commit-config.yaml` (ruff, YAML/TOML/JSON syntax, merge markers, private keys, large files, whitespace) on every commit. `pre-commit run --all-files` checks everything.

## Tests

- `tests/test_<module>.py`, written with `unittest` (`TestCase`, `subTest` for tables of cases); CI runs them through `scripts/ci_test_report.py`.
- Synthetic data only: plates like `00-000-00`, streets like `Example 1`, invented names. Fixtures live in `tests/fixtures/`.
- Every bug fix adds a test that fails without the fix. Regressions found after release go in `tests/test_historical_regressions.py` with a one-line docstring naming the original failure.
