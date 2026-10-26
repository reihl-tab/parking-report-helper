"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .municipality import (
    build_submissions,
    format_submission,
    load_config,
    submission_dict,
)
from .report import build_reports


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="parking-report",
        description="Turn a JSON list of cars into Hebrew parking-violation report texts.",
    )
    parser.add_argument("input", nargs="?", help="JSON file with a list of cars")
    parser.add_argument(
        "--intake",
        metavar="DIR",
        help="read report folders (report.json + photo each) from DIR instead of a cars file; "
        "see docs/INTAKE.md. Read-only: only records with status 'ready' are used",
    )
    parser.add_argument(
        "--drive-folder",
        metavar="ID",
        help="with --intake, first copy that Google Drive folder into DIR (read-only; needs "
        "a read-only access token in PARKING_DRIVE_TOKEN)",
    )
    parser.add_argument("--drive-api", help=argparse.SUPPRESS)  # local test server only
    parser.add_argument("--city", default="", help="city appended to the street")
    parser.add_argument("--name", default="", help="reporter name for the sign-off")
    parser.add_argument("--no-photo", action="store_true", help="omit the photo line")
    parser.add_argument(
        "--municipality",
        metavar="NAME_OR_FILE",
        help="print copy-paste form field values for a municipality "
        "(bundled name such as 'jerusalem', or a config .json path)",
    )
    parser.add_argument(
        "--reporter",
        metavar="FILE",
        help="JSON file with reporter details for the form (keep it outside the repo)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="with --municipality, print JSON instead of text",
    )
    return parser


def _read_json(path: str) -> object:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if bool(args.input) == bool(args.intake):
        parser.error("give exactly one of: a cars JSON file, or --intake DIR")
    if (args.drive_folder or args.drive_api) and not args.intake:
        parser.error("--drive-folder requires --intake")
    if args.drive_api and not args.drive_folder:
        parser.error("--drive-api requires --drive-folder")
    if not args.municipality:
        for flag, used in (
            ("--reporter", args.reporter),
            ("--json", args.json),
        ):
            if used:
                parser.error(f"{flag} requires --municipality")
    # The cars input is validated by its consumers (build_reports and
    # build_submissions take object and reject non-lists), so keep it untyped here.
    cars: object
    try:
        photos: list[str] = []
        if args.intake:
            if args.drive_folder:
                from .drive import API, download_folder

                download_folder(args.drive_folder, args.intake, args.drive_api or API)
            from .intake import read_intake

            found = read_intake(args.intake)
            cars, photos = found.cars, found.photos
            for name, reason in found.skipped:
                print(f"skipped {name}: {reason}", file=sys.stderr)
            for index, photo in enumerate(photos, start=1):
                print(f"car {index} photo: {photo or '(none)'}", file=sys.stderr)
        else:
            cars = _read_json(args.input)
        if args.municipality:
            config = load_config(args.municipality)
            reporter = _read_json(args.reporter) if args.reporter else {}
            if not isinstance(reporter, dict):
                raise ValueError("reporter file must be a JSON object")
            subs = build_submissions(cars, config, reporter, not args.no_photo)
            if args.json:
                output = json.dumps(
                    [submission_dict(rows) for rows in subs],
                    ensure_ascii=False,
                    indent=2,
                )
            else:
                url = config.get("channel", {}).get("url", "")
                blocks = [
                    f"# {i}/{len(subs)}  {url}\n{format_submission(rows)}"
                    for i, rows in enumerate(subs, start=1)
                ]
                output = "\n\n".join(blocks)
        else:
            reports = build_reports(
                cars, city=args.city, name=args.name, with_photo=not args.no_photo
            )
            output = "\n\n".join(reports)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
