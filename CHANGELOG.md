# Changelog

Flag, default, exit-code and JSON-key changes are recorded here, as required by the
[flag-stability policy](docs/CLI-CONTRACT.md#flag-stability-policy).

## Unreleased (planned 0.6.0)

- Docs: shared exit-code table for all four entry points in [CLI-CONTRACT.md](docs/CLI-CONTRACT.md#exit-codes), derived from the contract block's `exit_codes` and checked against it by `tests/test_cli_contract.py`; `CONTRIBUTING.md` and `docs/STYLE.md` now link the flag-stability policy (#73). Removed an empty `Unreleased` heading from this file. No flag, default or exit code changed, so no version change.

- `parking-report-106`: add the controlled shared-photo extension and the `--approved-extension-sha256` gate (#293, [design](docs/SHARED-PHOTO-EXTENSION-DESIGN.md), [docs](docs/SHARED-PHOTO.md)). One explicitly approved, separately verified car may be reported from the unchanged original photo of one completed ordinary vehicle intake. The parent's payload digest is corroborated against preserved private submission artifacts and a recompute of the original inputs; a provisioned private HMAC guard index (seed supplied only at runtime via `PARKING_GUARD_SEED`/`PARKING_GUARD_KEY_ID`) blocks the same plate under the same original, and a bounded private index allows exactly one extension per original. Parent entries and the original photo index stay byte-for-byte unchanged; the ledger relation stays plate-free; derived guard keys live only in the private index. Additive; minor version bump per the flag-stability policy.

## Unreleased (planned 0.5.0)

- `p106-stats build` and `refresh`: new optional `--neighborhoods PATH` (#225, [contract notes](docs/CLI-CONTRACT.md#notes-on-current-behaviour)). `PATH` is a UTF-8 JSON object mapping street name to neighborhood name; it fills `by_neighborhood` in `summary.json` (unmapped streets count as `(אחר)`) and adds the neighborhood chart to `report.html`. An unreadable or invalid file exits 1 with a fixed message that never includes the path or the file's contents. Without the flag `by_neighborhood` stays `null` and the output is unchanged. Additive; covered by the unreleased 0.5.0 minor bump, so no further version change.

- `parking-report-106`: add an explicit shared-photo manifest and the `--approved-shared-photo-sha256` gate (#271, [docs](docs/SHARED-PHOTO.md)). Two distinct cars in one unchanged original photo may each be reported as their own intake, with their own digest, receipt and state; the manifest is bound into each car's digest and is never permission by itself. Exactly two intakes and two distinct normalized plates; the group locks when its first car reserves a ledger slot. Each car's reservation also reads its sibling's record from the neighbouring intake folder and is refused (fail-closed) unless both manifests are identical. Ordinary photo dedupe is unchanged. Additive; minor version bump per the flag-stability policy.

- `parking-report-http`: `-h`/`--help` prints help and exits 0 before reading configuration or binding a socket (#188). New exit code 0 for help only; other arguments are still ignored, and normal startup is unchanged.

- Add an explicit parent-linked follow-up manifest and `--approved-followup-sha256` gate (#189). Preserve the original photo index and normal duplicate rejection; only one child per completed parent. Caller authority and live closure verification remain required.

- `parking-report-106`: after a successful submit, re-list today's cases and detect a server-side duplicate of the just-submitted text (#160). Duplicates exit with the new code 5 and print `duplicate_check: "duplicates_found"` plus `duplicate_case_numbers`; clean submissions print `duplicate_check: "single"` or `"pending"` and a failed check prints `"unavailable"` without failing the submission. New `api106.duplicate_case_numbers` helper. Minor version bump per the flag-stability policy.

- Adopted the CLI flag-stability policy (#73). No flag, default or exit code changed.
