"""Offline Hebrew monthly draft from the private ledger/reports.csv, never a sender.

Run: python -m parking_report.monthly PRIVATE_CSV --month YYYY-MM
Output can contain private references and must stay out of public logs/artifacts.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import date
from pathlib import Path
import re
import sys
from typing import Iterable, Mapping

REQUIRED = {
    "folder_id",
    "filed_date",
    "street",
    "event_type",
    "municipal_ref",
    "ref_mapping",
}


def _cell(value: str) -> str:
    """Keep untrusted ledger text inside one Markdown table cell."""
    return (
        value.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def monthly_draft(rows: Iterable[Mapping[str, str]], month: str) -> str:
    """Validate before rendering. Count ledger events, not unique plates or confirmed cases."""
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", month):
        raise ValueError("Invalid month")
    date.fromisoformat(month + "-01")
    selected = []
    seen = set()
    references = set()
    for row in rows:
        if not REQUIRED.issubset(row):
            raise ValueError("Missing ledger columns")
        if any(not isinstance(row[key], str) for key in REQUIRED):
            raise ValueError("Invalid ledger value")
        day = row["filed_date"]
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day):
            raise ValueError("Invalid filing date")
        date.fromisoformat(day)
        if day[:7] != month:
            continue
        for key in REQUIRED:
            if any(
                ord(char) < 32
                or 127 <= ord(char) <= 159
                or char in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
                for char in row[key]
            ):
                raise ValueError("Control characters in ledger")
        if not all(row[key].strip() for key in ("folder_id", "street", "event_type")):
            raise ValueError("Missing report identity or classification")
        if row["folder_id"] in seen:
            raise ValueError("Duplicate report identity")
        seen.add(row["folder_id"])
        ref = row["municipal_ref"].strip()
        # Preserve the complete reference. Do not infer a match from its prefix or order.
        if ref and not re.fullmatch(r"[0-9]+(?:-[0-9]+)?", ref):
            raise ValueError("Invalid municipal reference")
        if ref and ref in references:
            raise ValueError("Duplicate municipal reference; reconcile the ledger")
        if ref:
            references.add(ref)
        if row["ref_mapping"] == "verified" and not ref:
            raise ValueError("Verified mapping requires a reference")
        selected.append(dict(row, municipal_ref=ref))
    selected.sort(key=lambda row: (row["filed_date"], row["folder_id"]))
    streets = Counter(row["street"].strip() for row in selected)
    events = Counter(row["event_type"].strip() for row in selected)
    verified = sum(row["ref_mapping"] == "verified" for row in selected)
    lines = [
        f"# טיוטה: סיכום דיווחים לחודש {month}",
        "",
        "טיוטה לבדיקה בלבד. הנמענים והנוסח דורשים אישור לפני שליחה.",
        "",
        f"רשומות דיווח ביומן: {len(selected)}",
        f"שיוך מספר פנייה מאומת: {verified}",
        f"שיוך חסר או לא מאומת: {len(selected) - verified}",
        "",
        "הספירה היא של אירועי דיווח ביומן, לא של כלי רכב ייחודיים או תוצאות אכיפה.",
        "רחובות וסוגי אירוע נשענים על היומן; שיוך פנייה לא מאומת אינו מאשר את פרטי הפנייה בעירייה.",
        "",
        "## לפי רחוב",
        "",
        "| רחוב | דיווחים |",
        "| --- | ---: |",
    ]
    lines += [
        f"| {_cell(key)} | {count} |"
        for key, count in sorted(streets.items(), key=lambda item: (-item[1], item[0]))
    ]
    lines += ["", "## לפי סוג אירוע", "", "| סוג אירוע | דיווחים |", "| --- | ---: |"]
    lines += [
        f"| {_cell(key)} | {count} |"
        for key, count in sorted(events.items(), key=lambda item: (-item[1], item[0]))
    ]
    lines += [
        "",
        "## מספרי פנייה",
        "",
        "| תאריך | רחוב | סוג אירוע | מספר פנייה | שיוך |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in selected:
        status = "מאומת" if row["ref_mapping"] == "verified" else "לא מאומת"
        ref = row["municipal_ref"] or "חסר"
        lines.append(
            "| "
            + " | ".join(
                _cell(value)
                for value in (
                    row["filed_date"],
                    row["street"],
                    row["event_type"],
                    ref,
                    status,
                )
            )
            + " |"
        )
    if not selected:
        lines += ["", "אין רשומות בחודש הנבחר ביומן שסופק."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    parser.add_argument("--month", required=True)
    args = parser.parse_args(argv)
    try:
        with args.ledger.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not REQUIRED.issubset(reader.fieldnames or []):
                raise ValueError("Missing ledger columns")
            draft = monthly_draft(reader, args.month)
    except (OSError, UnicodeError, ValueError, csv.Error):
        print(
            "Monthly draft blocked: check the private ledger and month; no output was generated.",
            file=sys.stderr,
        )
        return 1
    print(draft, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
