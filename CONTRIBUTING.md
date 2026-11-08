# Contributing


## Issues: labels, milestones and order

The same scheme is used in every public repo. Do not add parallel label schemes.

**Labels** (every open issue has one type, one area and one priority)
- `type: feature | fix | task | docs | security` - the kind of work.
- `area: ...` - the part of the project (for example api, ci, containers, data, docs, quality, security). The list is per repo.
- `priority: high | medium | low` - high is done first.
- `status: blocked | held | in-progress | needs-review` - only when it applies. `status: in-progress` means someone claimed it.
- `needs-triage` - not sorted yet. Remove it when the three labels above are set.
- `good first issue` - small and clear, a good place to start.

**Milestones**
- `Now` - in progress or due this week. `Next` - planned after Now. `Later` - backlog.
- Every open issue is in exactly one. High priority goes to Now, medium to Next, low to Later, unless the maintainer decides otherwise. Repos may keep their own dated or goal milestones as well.

**How to pick an issue**
1. Open the repo's issues filtered by milestone `Now`, then `Next`.
2. Within a milestone, take `priority: high` first, then medium, then low. Prefer `good first issue` if you are new.
3. Skip anything with `status: blocked`, `status: held` or `status: in-progress`.
4. Comment "taking this" on the issue, then work from a fork and open a PR that references it.

**Project board.** New issues are added to the board automatically. Pull requests are not: the board workflow runs on issue events only, so no pull request can change the job that holds the board token (see #119). Referencing an issue (`fixes #N`) does not add the PR to the board as a separate item; it only lets readers follow the linked PR from the issue's card.

## File placement (enforced)

Decide where a file lives BEFORE creating it. The contract in `.repo-layout.yml` lists the allowed top-level directories, the file types permitted in each, and the required community-standards files; `python3 tools/check_repo_layout.py` enforces it in CI. A new kind of file means updating the contract in the same PR.

## Dependencies (hash-locked)

Python dependencies install only from the hash-locked files under `requirements/`, with `--require-hashes`; the project itself installs with `--no-deps --no-build-isolation` after `requirements/build.txt`. For development and tests:

```sh
python -m pip install --require-hashes -r requirements/build.txt -r requirements/api.txt
python -m pip install --no-deps --no-build-isolation .
python -m pip install --require-hashes -r requirements/ci.txt
```

A dependency change re-locks in the same PR. [docs/DEPENDENCY-LOCKS.md](docs/DEPENDENCY-LOCKS.md) has the commands, and `scripts/check_install_pins.py` (run by the tests) enforces it.

## CLI contract and versioning

The four console commands are the supported operator API. Their flags, defaults, exit codes and JSON keys are pinned in [docs/CLI-CONTRACT.md](docs/CLI-CONTRACT.md), with one [exit-code table](docs/CLI-CONTRACT.md#exit-codes) for all of them. Any change to them follows the [flag-stability policy](docs/CLI-CONTRACT.md#flag-stability-policy): update the contract block and the generated man pages, add a `CHANGELOG.md` entry, and apply the semver bump the policy names, in the same PR.
