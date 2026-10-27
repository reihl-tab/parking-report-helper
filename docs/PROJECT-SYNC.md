# Project board sync: design note

`scripts/project_sync.py`, run by `.github/workflows/project-sync.yml`, reads
the owner's GitHub Project (`users/reihlinstinct/projects/6`) and **proposes**
the board changes that a label or state change on an issue implies. It exists
because some contributors, Claude's sessions among them, can edit labels but
cannot reach user-level Projects.

## Pilot scope: proposal only

The pilot never writes to the board. The script contains no GraphQL mutation,
and its client refuses to send one before any network access. Each run logs
`proposal: set <Field> = <Option>` lines, plus `proposal: add the item to the
project` when the issue is not on the board yet. The maintainer applies them by
hand, or ignores them.

Why not write: the Projects v2 API has no conditional update. So a manual edit
made between a run's read and its write could still be overwritten. Manual-edit
preservation cannot be guaranteed while the sync writes, so it does not write.

## Trust boundary: no PR code with the token

The workflow runs only on `issues` events. For those events GitHub runs the
workflow file and the checked-out code from the default branch, and the
checkout pins `ref` to the default branch as well. A pull request therefore
cannot change the code that receives `PROJECT_PAT` until it is reviewed and
merged.

There is no `pull_request`, `pull_request_target` or `workflow_run` trigger.
Pull-request label changes are out of scope for the pilot. Credential-free PR
testing stays in `tests.yml`, which never sees the project token.

`tests/test_project_sync.py` enforces this:

- `project-sync.yml` triggers only on `issues`.
- It checks out the default branch and never a PR head.
- Any workflow that receives `PROJECT_PAT` and is triggered by a PR event may
  not check out or run repository code.

## Rule: manual board edits win (what is proposed)

Labels are an **input** to the board, not its source of truth. The board is
curated by hand, so the sync must never undo a manual edit.

Every proposal is a **compare-and-set** on one single-select field:

| Board shows | Labels said before this event | Action |
|---|---|---|
| the value the labels now give | any | nothing (already right) |
| nothing (empty field, or item not on the board yet) | any | propose |
| the value the labels gave **before** this event | same | propose: the board was in sync, so this is a label change to carry over |
| anything else | any | **no proposal**: someone edited the board by hand |

"Before this event" is derived from the webhook payload alone, with no stored
state:

- `labeled` / `unlabeled`: the current labels minus / plus `event.label`
- `closed`: the item was open
- `reopened`: the item was closed
- `opened` and anything else: unknown, so only empty fields get a proposal

If a priority label is removed and no priority rule matches any more, the
Priority field is left as it is. The sync never proposes clearing a field.

## Protected fields

`protected_fields` in `.github/project-sync.json` lists fields the sync never
proposes, even if a rule for them is added by mistake. The curated **Queue**
text field is protected. It is also not single-select, so there is no write
path for it in any case.

## Mapping (`.github/project-sync.json`)

- **Status:** merged/closed → `Done`; then `status: blocked` / `held` /
  `needs-review` / `in-progress`; default `Todo`.
  **Unverified:** these option names have not been checked against Project 6.
  Names that don't match are warned and skipped, never created, so a mismatch
  shows up in the proposal log instead of corrupting the board.
- **Priority:** `priority: high` → `P1`, `priority: medium` → `P2`.
  `priority: low` has no mapping, because the board has only P1/P2.
- **Queue:** never written.

## Before any write path

A future write path needs its own design review. It would need:

1. the Status option names confirmed on Project 6 (and P1/P2),
2. an answer to the read/write race above,
3. the credential decision (`PROJECT_PAT` scope), which is out of scope here.

## Limits

- **Stale proposal:** a proposal reflects the board at read time. Check the
  board before applying it by hand.
- **Issues only:** PR label and state changes produce no proposals.
- **Workflow file trust:** the regression tests check `project-sync.yml` and
  every other workflow that names `PROJECT_PAT`. They read the workflow text;
  they do not model GitHub's runtime. The existing `add-to-project.yml` gets
  `PROJECT_PAT` on `pull_request: opened`. On `pull_request` events, GitHub
  runs the workflow file from the PR's merge ref, so a same-repository PR that
  edits that file could change what receives the token. That workflow is not
  changed here; it is tracked in a separate issue.
- **Events with no "before":** for these (e.g. `opened`, `edited`), existing
  values never get a proposal. A board that drifted while labels were edited
  some other way stays as it is until the next label or state event.
- **Not verified against a real Project:** the GraphQL queries follow the
  documented Projects v2 schema, but they have not been run against a real
  board. Claude's sessions cannot read Projects.
