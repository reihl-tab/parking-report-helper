"""`p106-stats` command line: export file in, local statistics out (issue #210).

`build` writes machine-readable outputs (summary.json + CSVs) and a
self-contained RTL HTML report (report.html) with pure-SVG charts (#212).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from .stats import StatsError, load_incidents, plates_summary, summarize
from .stats_html import build_report

PROG = "p106-stats"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Generate local statistics from municipal 106 reports_raw.json "
            "exports. Fully offline: files are read locally, outputs are "
            "written locally, nothing is uploaded."
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
    return parser


def _write_csv(path: Path, header: tuple[str, ...], rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def run(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "build":  # pragma: no cover - argparse enforces
        return 2
    if args.top_streets < 1:
        print(f"{PROG}: --top-streets must be positive", file=sys.stderr)
        return 2
    try:
        incidents, duplicates = load_incidents(args.inputs)
    except StatsError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 1
    summary = summarize(incidents, top_streets=args.top_streets)
    summary["inputs"] = [Path(p).name for p in args.inputs]
    summary["duplicates_skipped"] = duplicates
    plate_rows: list[dict] = []
    if args.plates_ledger:
        try:
            plate_rows, skipped_ids = plates_summary(args.plates_ledger)
        except (OSError, StatsError) as exc:
            print(f"{PROG}: --plates-ledger: {exc}", file=sys.stderr)
            return 1
        if skipped_ids:
            print(
                f"{PROG}: --plates-ledger: {skipped_ids} row(s) excluded "
                "(no identifiers, invalid plate or invalid filed_date)",
                file=sys.stderr,
            )
    summary["repeat_plates_count"] = len(plate_rows)

    out_dir = Path(args.output)
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
    print(
        f"{PROG}: {summary['total_incidents']} incidents "
        f"({duplicates} duplicates skipped) -> {out_dir}"
    )
    return 0


def main() -> None:
    raise SystemExit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
