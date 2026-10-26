# Project board sync: design note

`scripts/project_sync.py`, run by `.github/workflows/project-sync.yml`, carries
label and state changes on issues and PRs onto the owner's GitHub Project
(`users/reihlinstinct/projects/6`). It exists because some contributors,
Claude's sessions among them, can edit labels but cannot reach user-level
Projects.

## Rule: manual board edits win

Labels are an **input** to the board, not its source of truth. The board is
curated by hand, so the sync must never undo a manual edit.

Every write is a **compare-and-set** on one single-select field:

| Board shows | Labels said before this event | Action |
|---|---|---|
| the value the labels now give | any | nothing (already right) |
| nothing (empty field, or item not on the board yet) | any | set |
| the value the labels gave **before** this event | same | set: the board was in sync, so this is a label change to carry over |
| anything else | any | **leave it**: someone edited the board by hand |

"Before this event" is derived from the webhook payload alone, with no stored
state:

- `labeled` / `unlabeled`: the current labels minus / plus `event.label`
- `closed`: the item was open
- `reopened`: the item was closed
- `opened` and anything else: unknown, so only empty fields are filled

If a priority label is removed and no priority rule matches any more, the
Priority field is left as it is. The sync never clears a field.

## Protected fields

`protected_fields` in `.github/project-sync.json` lists fields the sync never
writes, even if a rule for them is added by mistake. The curated **Queue**
text field is protected. It is also not single-select, so there is no write
path for it in any case.

## Mapping (`.github/project-sync.json`)

- **Status:** merged/closed → `Done`; then `status: blocked` / `held` /
  `needs-review` / `in-progress`; default `Todo`.
  **Unverified:** these option names have not been checked against Project 6.
  Names that don't match are warned and skipped, never created, so a mismatch
  shows up in the dry-run log instead of corrupting the board.
- **Priority:** `priority: high` → `P1`, `priority: medium` → `P2`.
  `priority: low` has no mapping, because the board has only P1/P2.
- **Queue:** never written.

## Turning it on

The sync stays a dry run until the repository variable `PROJECT_SYNC` is `on`.
The dry run reads the project and the item's current values and prints what
it would set and which manual values it would leave alone. It changes
nothing.

Before turning it on, the maintainer:

1. confirms the Status option names on Project 6 (and P1/P2),
2. checks a dry-run log on a labelled issue,
3. makes the credential decision (`PROJECT_PAT`), which is out of scope here.

## Limits

- **Race:** a manual edit made between this run's read and its write can be
  overwritten. The window is one run of a few seconds, and runs for the same
  issue are serialized by the workflow's `concurrency` group.
- **Events with no "before":** for these (e.g. `opened`, `edited`), existing
  values are never changed. A board that drifted while labels were edited
  some other way stays as it is until the next label or state event.
- **Not verified against a real Project:** the GraphQL queries follow the
  documented Projects v2 schema, but they have not been run against a real
  board. Claude's sessions cannot read Projects.
