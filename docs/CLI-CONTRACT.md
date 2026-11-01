# CLI contract (v0, current behaviour)

Issue #73. This file **documents what the four entry points do today**. It
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
      "argv": "ignored (not parsed, not rejected), except -h/--help",
      "exit_codes": {"0": "-h/--help: help text on stdout, before configuration is read or a socket is bound", "1": "invalid configuration: fixed message on stderr, no exception text"},
      "config_error_message": "HTTP service configuration is invalid; check private environment",
      "json_output": "HTTP responses only; see docs/HTTP106.md"
    },
    "p106-stats": {
      "module": "parking_report.stats_cli:main",
      "positionals": ["command"],
      "options": {},
      "subcommands": {
        "build": {
          "positionals": ["inputs"],
          "options": {
            "--output": {"default": null, "required": true},
            "--top-streets": {"default": 20}
          }
        }
      },
      "exit_codes": {"0": "summary.json and the CSV tables written to the output directory; one summary line on stdout", "1": "input error (missing, unreadable or invalid export file), `p106-stats: ...` on stderr", "2": "usage error (argparse, or an invalid value such as --top-streets 0), message on stderr"}
    }
  }
}
```
<!-- cli-contract:end -->

## Notes on current behaviour

- **`parking-report`** needs exactly one of `input` or `--intake DIR`. `--drive-folder` requires `--intake`, `--drive-api` requires `--drive-folder` (it's for a local test server only and is hidden from `--help`), and `--reporter` and `--json` require `--municipality`. A broken rule exits 2 with an argparse usage message.
- **`parking-report-106`** prints exactly one JSON line for every outcome *after argument parsing succeeds*: `dry_run` or `submit` (exit 0), the fixed `blocked` line (exit 4), or a successful submit whose post-submit duplicate check found a second municipal case with the identical text (exit 5, with `duplicate_check: "duplicates_found"` and the case numbers in `duplicate_case_numbers`; every submit line carries `duplicate_check` and `duplicate_case_numbers`: a fresh submission prints `"single"`, `"pending"` (the new case is not visible in the municipal listing yet), or `"unavailable"` when the check itself failed, none of which change the exit code, and any other submit result prints `"skipped"` with an empty list). Every `Exception` after parsing becomes that `blocked` line (a `BaseException` such as `KeyboardInterrupt` or `SystemExit` still propagates), and no exception text reaches the log. Before that point argparse owns the output: `--help` prints help text and exits 0, and a usage error prints usage text to stderr and exits 2. Neither is JSON. A live submission additionally needs `PARKING_106_ENABLE_LIVE=true`, the exact `--approved-sha256` and `--ledger-dir`; otherwise it ends `blocked`.
- **`parking-report-http`** reads only `-h`/`--help` from the command line (#188): either one, anywhere in the arguments, prints help on stdout and exits 0 before any configuration is read or a socket is bound. Every other argument is still **ignored, not rejected**. Rejecting them would be a behaviour change for a separate PR. A missing or short `PARKING_HTTP_TOKEN` or an invalid `PORT` exits 1 with the fixed message in `config_error_message`. Otherwise it serves until it's stopped.

- **`p106-stats`** is fully offline: `build` reads one or more `reports_raw.json` exports and writes `summary.json` and the CSV tables into `--output`. A missing or invalid export exits 1; `--top-streets` must be positive (exit 2 otherwise).

## Man pages (#188)

The four console commands in `pyproject.toml` `[project.scripts]` are the
**shipped** commands and each has a page in `man/man1/`:
`parking-report.1`, `parking-report-106.1`, `parking-report-http.1` and
`p106-stats.1`.
Everything under `scripts/` is **internal** tooling and has no page.

The pages are generated, not hand-edited. `scripts/gen_manpages.py` reads the
JSON block above (flags, defaults, choices, exit codes) and each command's own
argparse definitions in-process (description, option help), without running
the command, reading configuration, binding a socket or using the network.
`tests/test_manpages_188.py` fails when a committed page differs from the
generator's output. Generation and `--check` import the package and all four
command modules, so they need the existing `api` extra installed
(`pip install ".[api]"`, as for the tests); the installer itself only copies
files.

```sh
python scripts/gen_manpages.py            # regenerate man/man1/*.1
python scripts/gen_manpages.py --check    # exit 1 on drift
python scripts/install_manpages.py        # $DESTDIR$PREFIX/share/man/man1
PREFIX=$HOME/.local python scripts/install_manpages.py
DESTDIR=/tmp/stage PREFIX=/usr python scripts/install_manpages.py
```

`PREFIX` defaults to `/usr/local` and must be absolute; `DESTDIR` is empty by
default. `--prefix`/`--destdir` override them. The installer copies only the
shipped pages, needs no network and never calls `sudo`. Both scripts are
quiet by default; `--debug` or `PARKING_MANPAGES_DEBUG=1` prints stage names
to stderr. Logs never contain arguments, paths, environment values or
configuration, and a failure prints only the stage and the error class.

Path safety (#207). `PREFIX` must not contain a `..` component, so
`$DESTDIR$PREFIX` cannot climb out of the staging directory (exit 1,
`stage=destination error=ValueError`). The trusted root is `DESTDIR` when it
is set, otherwise `PREFIX`: it and its parents are used as they are (they may
be symlinks). Every directory below it, and every existing page, must be a
real directory or regular file; a symlink there is refused (exit 1,
`stage=copy error=UnsafePathError`) before any page is written. Pages are
written to a new temporary file in `man1` and swapped in with `os.replace`,
so an existing link or hard-linked file is never written through or
`chmod`-ed. The installer is not safe against a concurrent attacker: run it
only on directories nobody else can write to while it runs.

Type checks: the configured `mypy` run covers `src` only. The two scripts
check clean when run explicitly with the source tree on the path,
`MYPYPATH=src mypy scripts/gen_manpages.py scripts/install_manpages.py`
(also with `--strict`); CI does not run that.

After changing a flag, help text or exit code, update the JSON block and run
the generator in the same PR.

Page date (#206). The `.TH` date on every page is the fixed `PAGE_DATE`
string in `scripts/gen_manpages.py`. Generation never reads the wall clock,
the time zone, the locale, `SOURCE_DATE_EPOCH` or any other environment
setting for it, so the same tree always gives the same bytes
(`tests/test_manpages_date_206.py`). Keeping the date current is a **manual
release checklist, not an automated check**: nothing compares `PAGE_DATE`
with a release date or fails when it is old. When a maintainer changes the
package version for a release (`version` in `pyproject.toml`,
`parking_report.__version__` and `package_version` in the JSON block above):

1. Set `PAGE_DATE` to the release date (`YYYY-MM-DD`) in the same PR.
2. With the `api` extra installed, run `python scripts/gen_manpages.py`,
   then `python scripts/gen_manpages.py --check` (exit 0).
3. Commit the regenerated `man/man1/*.1` with the version change.

A `PAGE_DATE` change without step 2 makes `--check` and the drift test fail.
Between releases the date stays as it is, even when pages are regenerated
for a help-text change.

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
