"""`p106-stats` command line: export file in, local statistics out (issue #210).

`build` writes machine-readable outputs (summary.json + CSVs) and a
self-contained RTL HTML report (report.html) with pure-SVG charts (#212).
`refresh` fetches the reporter's current cases from GetContactCases,
unions them with archived exports by case_number and runs the same build
(parking-stats #13).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

from .api106 import APIError, InputError, Municipality
from .stats import (
    StatsError,
    load_incidents,
    plates_summary,
    summarize,
    union_with_fetched,
)
from .stats_html import build_report

PROG = "p106-stats"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Generate local statistics from municipal 106 reports_raw.json "
            "exports. `build` is fully offline: files are read locally, "
            "outputs are written locally, nothing is uploaded. `refresh` "
            "contacts the municipal API to fetch the reporter's cases."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="build statistics from export files")
    build.add_argument("inputs", nargs="+", help="reports_raw.json export file(s)")
    build.add_argument(
        "--output",
        "-o",
        required=True,
        help="output directory for summary.json, the CSV tables and report.html",
    )
    build.add_argument(
        "--top-streets",
        type=int,
        default=20,
        help="how many streets to keep in by_street (default: 20)",
    )
    build.add_argument(
        "--plates-ledger",
        default=None,
        help="optional private filing-ledger CSV; adds a repeat-plates "
        "aggregate (plates.csv with plate,reports for 3+ distinct "
        "filed reports) - the ledger itself never leaves the machine",
    )
    refresh = sub.add_parser(
        "refresh",
        help="fetch current cases from GetContactCases, union with archived "
        "exports by case_number and rebuild",
    )
    refresh.add_argument(
        "--output",
        "-o",
        required=True,
        help="output directory for summary.json, the CSV tables and report.html",
    )
    refresh.add_argument(
        "--archive",
        action="append",
        default=[],
        help="archived reports_raw.json export (repeatable); fetched rows win "
        "when a case_number appears in both",
    )
    refresh.add_argument(
        "--from",
        dest="from_date",
        default="2021-01-01",
        help="first case date to fetch, YYYY-MM-DD (default: 2021-01-01)",
    )
    refresh.add_argument(
        "--top-streets",
        type=int,
        default=20,
        help="how many streets to keep in by_street (default: 20)",
    )
    refresh.add_argument(
        "--plates-ledger",
        default=None,
        help="optional private filing-ledger CSV (same aggregation as build)",
    )
    refresh.add_argument(
        "--save-union",
        default=None,
        help="optional path for the merged raw export (PRIVATE: contains case "
        "texts; keep outside any published repository)",
    )
    return parser


def _write_csv(path: Path, header: tuple[str, ...], rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _fetch_incidents(
    credentials: dict, reporter: dict[str, str], from_date: str
) -> list[dict]:
    """GET the reporter's cases; returns raw incident dicts. Network + creds."""
    api = Municipality(credentials)
    token = api.login()
    data = api.list_cases(token, reporter, from_date=from_date)
    incidents = data.get("incidents") or []
    if not isinstance(incidents, list) or any(
        not isinstance(row, dict) for row in incidents
    ):
        raise StatsError("GetContactCases returned an unexpected shape")
    return incidents


def _refresh(args) -> int:
    subscription_key = os.environ.get("PARKING_106_SUBSCRIPTION_KEY", "")
    username = os.environ.get("PARKING_106_USERNAME", "")
    password = os.environ.get("PARKING_106_PASSWORD", "")
    id_number = os.environ.get("PARKING_REPORTER_ID_NUMBER", "")
    if not all((subscription_key, username, password, id_number)):
        print(
            f"{PROG}: refresh needs PARKING_106_SUBSCRIPTION_KEY, "
            "PARKING_106_USERNAME, PARKING_106_PASSWORD and "
            "PARKING_REPORTER_ID_NUMBER in the environment",
            file=sys.stderr,
        )
        return 1
    # strptime alone is lenient about zero-padding ("2026-1-5" parses),
    # but the municipal API rejects that shape after login - reject it
    # here, before any network call (#237).
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.from_date):
        print(
            f"{PROG}: --from must be a YYYY-MM-DD date, got {args.from_date!r}",
            file=sys.stderr,
        )
        return 2
    try:
        dt.datetime.strptime(args.from_date, "%Y-%m-%d")
    except ValueError:
        print(
            f"{PROG}: --from must be a YYYY-MM-DD date, got {args.from_date!r}",
            file=sys.stderr,
        )
        return 2
    # Validate the local inputs before any network call or login.
    try:
        archived: list[dict] = []
        duplicates = 0
        if args.archive:
            archived, duplicates = load_incidents(args.archive)
    except StatsError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 1
    # The ledger is a local file: a missing/unreadable one must fail
    # here, not after the fetch and union write (#237).
    plate_rows, code = _plates(args)
    if code:
        return code
    credentials = {
        "subscription_key": subscription_key,
        "login": {"UserName": username, "Password": password},
    }
    reporter = {"ContactIdNumber": id_number, "ContactIdType": "100000000"}
    try:
        fetched = _fetch_incidents(credentials, reporter, args.from_date)
        incidents, overlaps = union_with_fetched(archived, fetched)
    except (StatsError, InputError, APIError) as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 1
    if args.save_union:
        try:
            # The union holds private case texts: 0600 like the registry
            # export, never the default umask (#237).
            fd = os.open(args.save_union, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            # os.open's mode applies only at creation; a pre-existing
            # 0644 union file keeps its mode without this (#237 review).
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(
                    json.dumps({"incidents": incidents}, ensure_ascii=False) + "\n"
                )
        except OSError as exc:
            print(f"{PROG}: --save-union: {exc}", file=sys.stderr)
            return 1
    summary = summarize(incidents, top_streets=args.top_streets)
    summary["inputs"] = [Path(p).name for p in args.archive] + ["GetContactCases"]
    summary["duplicates_skipped"] = duplicates
    summary["repeat_plates_count"] = len(plate_rows)
    _write_outputs(summary, plate_rows, Path(args.output))
    print(
        f"{PROG}: refresh: {len(fetched)} fetched "
        f"({overlaps} refreshed archived rows), "
        f"{summary['total_incidents']} incidents -> {args.output}"
    )
    return 0


def _write_outputs(summary: dict, plate_rows: list[dict], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_csv(
        out_dir / "monthly.csv", ("month", "reports"), summary["by_month"].items()
    )
    _write_csv(
        out_dir / "categories.csv",
        ("category", "reports"),
        summary["by_category"].items(),
    )
    _write_csv(
        out_dir / "streets.csv", ("street", "reports"), summary["by_street"].items()
    )
    plates_path = out_dir / "plates.csv"
    if plate_rows:
        _write_csv(
            plates_path,
            ("plate", "reports"),
            ((r["plate"], r["reports"]) for r in plate_rows),
        )
    elif plates_path.exists():
        # Any build that does not emit plates.csv removes a stale one
        # (with or without the flag) so it cannot be published by mistake.
        plates_path.unlink()
    (out_dir / "report.html").write_text(build_report(summary), encoding="utf-8")


def _plates(args) -> tuple[list[dict], int]:
    """Shared --plates-ledger handling; returns (rows, exit_code)."""
    if not args.plates_ledger:
        return [], 0
    try:
        plate_rows, skipped_ids = plates_summary(args.plates_ledger)
    except (OSError, StatsError) as exc:
        print(f"{PROG}: --plates-ledger: {exc}", file=sys.stderr)
        return [], 1
    if skipped_ids:
        print(
            f"{PROG}: --plates-ledger: {skipped_ids} row(s) excluded "
            "(no identifiers, invalid plate or invalid filed_date)",
            file=sys.stderr,
        )
    return plate_rows, 0


def _build(args) -> int:
    try:
        incidents, duplicates = load_incidents(args.inputs)
    except StatsError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 1
    summary = summarize(incidents, top_streets=args.top_streets)
    summary["inputs"] = [Path(p).name for p in args.inputs]
    summary["duplicates_skipped"] = duplicates
    plate_rows, code = _plates(args)
    if code:
        return code
    summary["repeat_plates_count"] = len(plate_rows)
    _write_outputs(summary, plate_rows, Path(args.output))
    print(
        f"{PROG}: {summary['total_incidents']} incidents "
        f"({duplicates} duplicates skipped) -> {args.output}"
    )
    return 0


def run(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.top_streets < 1:
        print(f"{PROG}: --top-streets must be positive", file=sys.stderr)
        return 2
    if args.command == "build":
        return _build(args)
    if args.command == "refresh":
        return _refresh(args)
    return 2  # pragma: no cover - argparse enforces


def main() -> None:
    raise SystemExit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
