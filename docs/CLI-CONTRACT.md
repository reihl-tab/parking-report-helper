# CLI contract (v0, current behaviour)

Issue #73. This file **documents what the three entry points do today**. It
doesn't change them. `tests/test_cli_contract.py` loads the JSON block below
and checks it against the real parsers and exit paths. Changing a flag, a
default or an exit code without updating this file fails CI. The package
version is `0.4.0`. Changes to this contract follow the
[flag-stability policy](#flag-stability-policy).

## Machine-readable contract

<!-- cli-contract:begin -->
```json
{
  "contract_version": 0,
  "package_version": "0.4.0",
  "commands": {
    "parking-report": {
      "module": "parking_report.cli:main",
      "positionals": ["input"],
      "options": {
        "--intake": {"default": null},
        "--drive-folder": {"default": null},
        "--drive-api": {"default": null, "hidden": true},
        "--city": {"default": ""},
        "--name": {"default": ""},
        "--no-photo": {"default": false, "flag": true},
        "--municipality": {"default": null},
        "--reporter": {"default": null},
        "--json": {"default": false, "flag": true}
      },
      "exit_codes": {"0": "report text (or --json submissions) on stdout; also --help text", "1": "input/config error, `error: ...` on stderr", "2": "usage error (argparse or a flag-combination rule), usage text on stderr"},
      "json_output": "only with --municipality"
    },
    "parking-report-106": {
      "module": "parking_report.api_cli:main",
      "positionals": ["folder"],
      "options": {
        "--addresses": {"default": null, "required": true},
        "--state-dir": {"default": ".106-state"},
        "--ledger-dir": {"default": null},
        "--submit": {"default": false, "flag": true},
        "--approved-sha256": {"default": ""},
        "--approved-followup-sha256": {"default": ""},
        "--retry-photo": {"default": false, "flag": true},
        "--approved-category-routing": {"default": "", "choices": ["crosswalk_parking", "blocked_ramp"], "deprecated": true}
      },
      "exit_codes": {"0": "one JSON line: dry_run or submit result; --help prints argparse help text instead", "2": "usage error: argparse usage text on stderr, no JSON", "4": "blocked: fixed JSON line, no exception text", "5": "submit succeeded but the duplicate check found a second municipal case with the same text; case numbers in duplicate_case_numbers"},
      "json_output": "one line on stdout for every outcome after successful argument parsing",
      "stdout_keys": {
        "dry_run": ["status", "sha256", "photo_upload_bytes", "network_requests"],
        "submit": ["status", "sha256", "photo_attached", "duplicate_check", "duplicate_case_numbers"],
        "blocked": ["status", "note"]
      }
    },
    "parking-report-http": {
      "module": "parking_report.http_service:main",
      "positionals": [],
      "options": {},
      "environment": ["PARKING_HTTP_TOKEN", "PORT"],
      "argv": "ignored (not parsed, not rejected)",
      "exit_codes": {"1": "invalid configuration: fixed message on stderr, no exception text"},
      "config_error_message": "HTTP service configuration is invalid; check private environment",
      "json_output": "HTTP responses only; see docs/HTTP106.md"
    }
  }
}
```
<!-- cli-contract:end -->

## Notes on current behaviour

- **`parking-report`** needs exactly one of `input` or `--intake DIR`. `--drive-folder` requires `--intake`, `--drive-api` requires `--drive-folder` (it's for a local test server only and is hidden from `--help`), and `--reporter` and `--json` require `--municipality`. A broken rule exits 2 with an argparse usage message.
- **`parking-report-106`** prints exactly one JSON line for every outcome *after argument parsing succeeds*: `dry_run` or `submit` (exit 0), the fixed `blocked` line (exit 4), or a successful submit whose post-submit duplicate check found a second municipal case with the identical text (exit 5, with `duplicate_check: "duplicates_found"` and the case numbers in `duplicate_case_numbers`; every submit line carries `duplicate_check` and `duplicate_case_numbers`: a fresh submission prints `"single"`, `"pending"` (the new case is not visible in the municipal listing yet), or `"unavailable"` when the check itself failed, none of which change the exit code, and any other submit result prints `"skipped"` with an empty list). Every `Exception` after parsing becomes that `blocked` line (a `BaseException` such as `KeyboardInterrupt` or `SystemExit` still propagates), and no exception text reaches the log. Before that point argparse owns the output: `--help` prints help text and exits 0, and a usage error prints usage text to stderr and exits 2. Neither is JSON. A live submission additionally needs `PARKING_106_ENABLE_LIVE=true`, the exact `--approved-sha256` and `--ledger-dir`; otherwise it ends `blocked`.
- **`parking-report-http`** doesn't read command-line arguments: `main()` takes none and never looks at `sys.argv`, so extra arguments are **ignored, not rejected**. Rejecting them would be a behaviour change for a separate PR. A missing or short `PARKING_HTTP_TOKEN` or an invalid `PORT` exits 1 with the fixed message in `config_error_message`. Otherwise it serves until it's stopped.

## Known gaps (measured, not fixed here)

1. `--json` isn't available everywhere. `parking-report` has it only with `--municipality`; plain report text has no JSON mode.
2. Exit codes aren't shared across commands: `1` means input error in `parking-report`, and `4` means blocked in `parking-report-106`.
3. `parking-report-106`'s `prog` comes from `sys.argv[0]`, and its parser is built inside `main()`, not a `build_parser()`.
4. Resolved by the [flag-stability policy](#flag-stability-policy): flag changes need a `CHANGELOG.md` entry.

## Flag-stability policy

Adopted by the maintainer in #73 (decision 6433915118). It applies to the
flags, defaults, exit codes and JSON keys in the block above.

1. **Supported API.** The CLI and this contract are the supported operator
   API.
2. **Breaking changes.** Removing or renaming a flag, or changing a default,
   an exit code or a documented JSON key, needs a **minor** version bump
   before 1.0 and a **major** bump from 1.0 on. The same PR states the
   compatibility impact and the migration, updates this file and adds a
   `CHANGELOG.md` entry.
3. **Deprecation before removal.** A normal removal or rename is announced
   as a deprecation first, and the old behaviour keeps working for at least
   one released minor version. `--approved-category-routing` is deprecated
   today.
4. **Additive changes.** A new flag, exit code or JSON key is not exempt. It
   needs a contract update, tests, a `CHANGELOG.md` entry and the right
   semver bump.
5. **Security and privacy exception.** When keeping the old behaviour would
   keep an exposure, a fix may fail closed immediately, without the
   deprecation period. The PR documents the exception and the migration. A
   dangerous flag is never kept working just to honour a deprecation.
6. **Gates stay.** This policy never weakens an existing safety or approval
   gate (for example live-submission enablement, the exact reviewed digest,
   or the durable ledger).

Follow-ups that need their own PRs: `--json` for plain report output, and a
shared exit-code table.
