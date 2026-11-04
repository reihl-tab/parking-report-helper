# Changelog

Flag, default, exit-code and JSON-key changes are recorded here, as required by the
[flag-stability policy](docs/CLI-CONTRACT.md#flag-stability-policy).

## Unreleased (planned 0.5.0)

- `p106-stats build` and `refresh`: new optional `--neighborhoods PATH` (#225, [contract notes](docs/CLI-CONTRACT.md#notes-on-current-behaviour)). `PATH` is a UTF-8 JSON object mapping street name to neighborhood name; it fills `by_neighborhood` in `summary.json` (unmapped streets count as `(אחר)`) and adds the neighborhood chart to `report.html`. An unreadable or invalid file exits 1 with a fixed message that never includes the path or the file's contents. Without the flag `by_neighborhood` stays `null` and the output is unchanged. Additive; covered by the unreleased 0.5.0 minor bump, so no further version change.

- `parking-report-106`: add an explicit shared-photo manifest and the `--approved-shared-photo-sha256` gate (#271, [docs](docs/SHARED-PHOTO.md)). Two distinct cars in one unchanged original photo may each be reported as their own intake, with their own digest, receipt and state; the manifest is bound into each car's digest and is never permission by itself. Exactly two intakes and two distinct normalized plates; the group locks when its first car reserves a ledger slot. Each car's reservation also reads its sibling's record from the neighbouring intake folder and is refused (fail-closed) unless both manifests are identical. Ordinary photo dedupe is unchanged. Additive; minor version bump per the flag-stability policy.

- `parking-report-http`: `-h`/`--help` prints help and exits 0 before reading configuration or binding a socket (#188). New exit code 0 for help only; other arguments are still ignored, and normal startup is unchanged.

- Add an explicit parent-linked follow-up manifest and `--approved-followup-sha256` gate (#189). Preserve the original photo index and normal duplicate rejection; only one child per completed parent. Caller authority and live closure verification remain required.

- `parking-report-106`: after a successful submit, re-list today's cases and detect a server-side duplicate of the just-submitted text (#160). Duplicates exit with the new code 5 and print `duplicate_check: "duplicates_found"` plus `duplicate_case_numbers`; clean submissions print `duplicate_check: "single"` or `"pending"` and a failed check prints `"unavailable"` without failing the submission. New `api106.duplicate_case_numbers` helper. Minor version bump per the flag-stability policy.

- Adopted the CLI flag-stability policy (#73). No flag, default or exit code changed.

## Unreleased
