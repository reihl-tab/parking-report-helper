"""`p106-stats` command line: export file in, local statistics out (issue #210).

Milestone 1 writes machine-readable outputs (summary.json + CSVs). Charts and
the self-contained HTML report land in the follow-up PRs on the same issue.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from .stats import StatsError, load_incidents, summarize

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
        help="output directory for summary.json and the CSV tables",
    )
    build.add_argument(
        "--top-streets",
        type=int,
        default=20,
        help="how many streets to keep in by_street (default: 20)",
    )
    return parser


def _write_csv(path: Path, header: tuple[str, str], rows) -> None:
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
    summary["inputs"] = [str(Path(p)) for p in args.inputs]
    summary["duplicates_skipped"] = duplicates

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
    print(
        f"{PROG}: {summary['total_incidents']} incidents "
        f"({duplicates} duplicates skipped) -> {out_dir}"
    )
    return 0


def main() -> None:
    raise SystemExit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
