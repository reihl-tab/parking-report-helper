# CLI contract (v0, current behaviour)

Issue #73. This file **documents what the three entry points do today**. It
doesn't change them. `tests/test_cli_contract.py` loads the JSON block below
and checks it against the real parsers and exit paths. Changing a flag, a
default or an exit code without updating this file fails CI. The package
version is `0.2.0`, so nothing here is a stability promise yet; see
[Proposed policy](#proposed-policy-not-adopted).

## Machine-readable contract

<!-- cli-contract:begin -->
```json
{
  "contract_version": 0,
  "package_version": "0.2.0",
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
        "--retry-photo": {"default": false, "flag": true},
        "--approved-category-routing": {"default": "", "choices": ["crosswalk_parking", "blocked_ramp"], "deprecated": true}
      },
      "exit_codes": {"0": "one JSON line: dry_run or submit result; --help prints argparse help text instead", "2": "usage error: argparse usage text on stderr, no JSON", "4": "blocked: fixed JSON line, no exception text"},
      "json_output": "one line on stdout for every outcome after successful argument parsing",
      "stdout_keys": {
        "dry_run": ["status", "sha256", "photo_upload_bytes", "network_requests"],
        "submit": ["status", "sha256", "photo_attached"],
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
- **`parking-report-106`** prints exactly one JSON line for every outcome *after argument parsing succeeds*: `dry_run` or `submit` (exit 0), or the fixed `blocked` line (exit 4). Every `Exception` after parsing becomes that `blocked` line (a `BaseException` such as `KeyboardInterrupt` or `SystemExit` still propagates), and no exception text reaches the log. Before that point argparse owns the output: `--help` prints help text and exits 0, and a usage error prints usage text to stderr and exits 2. Neither is JSON. A live submission additionally needs `PARKING_106_ENABLE_LIVE=true`, the exact `--approved-sha256` and `--ledger-dir`; otherwise it ends `blocked`.
- **`parking-report-http`** doesn't read command-line arguments: `main()` takes none and never looks at `sys.argv`, so extra arguments are **ignored, not rejected**. Rejecting them would be a behaviour change for a separate PR. A missing or short `PARKING_HTTP_TOKEN` or an invalid `PORT` exits 1 with the fixed message in `config_error_message`. Otherwise it serves until it's stopped.

## Known gaps (measured, not fixed here)

1. `--json` isn't available everywhere. `parking-report` has it only with `--municipality`; plain report text has no JSON mode.
2. Exit codes aren't shared across commands: `1` means input error in `parking-report`, and `4` means blocked in `parking-report-106`.
3. `parking-report-106`'s `prog` comes from `sys.argv[0]`, and its parser is built inside `main()`, not a `build_parser()`.
4. There's no CHANGELOG entry policy for flag changes.

## Proposed policy (not adopted)

This is a proposal for the owner to decide on; it isn't in force yet.
- The CLI is the supported operator API.
- Removing or renaming a flag, changing a default or changing an exit code needs a minor version bump before 1.0 and a major one after, plus a CHANGELOG line and an update to this file.
- Adding a flag needs only an update to this file.
- Deprecated flags (currently `--approved-category-routing`) keep working for at least one minor release after the deprecation is announced.
- `--json` for plain report output, and a shared exit-code table, would be separate follow-up PRs.
