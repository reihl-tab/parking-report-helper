"""Offline PRIVATE status table and seven-day reminder drafts; never sends anything."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path

REQUIRED = {
    "folder_id",
    "filed_date",
    "street",
    "event_type",
    "municipal_ref",
    "ref_mapping",
}
TEXT_FIELDS = REQUIRED | {
    "plate",
    "x_post_url",
    "facebook_post_url",
    "municipal_response",
    "response_checked_at",
    "response_evidence",
    "receipt_evidence",
    "last_reminder_at",
}
REMINDER_DAYS = 7


def _day(value: str) -> date:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("Invalid date")
    return date.fromisoformat(value)


def _safe(value: str) -> str:
    # Untrusted ledger values must not create links, images, markup or table rows.
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_{}\[\]()!|])", r"\\\1", value)


def status_draft(rows: Iterable[Mapping[str, str]], as_of: str) -> str:
    """A current explicit no-reply check is required; absent fields mean unknown."""
    today = _day(as_of)
    reports = []
    ids: set[str] = set()
    refs: set[str] = set()
    for raw in rows:
        if not REQUIRED.issubset(raw):
            raise ValueError("Missing ledger columns")
        row = {key: raw.get(key, "") for key in TEXT_FIELDS}
        for value in row.values():
            if not isinstance(value, str) or any(
                ord(c) < 32
                or 127 <= ord(c) <= 159
                or c in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
                for c in value
            ):
                raise ValueError("Unsafe ledger value")
        if not all(row[key].strip() for key in ("folder_id", "street", "event_type")):
            raise ValueError("Missing report identity")
        filed = _day(row["filed_date"])
        if filed > today:
            raise ValueError("Future filing date")
        if row["folder_id"] in ids:
            raise ValueError("Duplicate report ID")
        ids.add(row["folder_id"])
        ref = row["municipal_ref"].strip()
        if ref and (not re.fullmatch(r"[0-9]+(?:-[0-9]+)?", ref) or ref in refs):
            raise ValueError("Invalid or duplicate reference")
        if ref:
            refs.add(ref)
        row["municipal_ref"] = ref
        response = row["municipal_response"] or "unknown"
        if response not in {"unknown", "no_reply", "received"}:
            raise ValueError("Unknown response status")
        checked = (
            _day(row["response_checked_at"]) if row["response_checked_at"] else None
        )
        if checked and not filed <= checked <= today:
            raise ValueError("Invalid response check date")
        if response != "unknown" and (
            not checked or not row["response_evidence"].strip()
        ):
            raise ValueError("Response state requires checked date and source evidence")
        reminded = _day(row["last_reminder_at"]) if row["last_reminder_at"] else None
        if reminded and not filed <= reminded <= today:
            raise ValueError("Invalid reminder date")
        age = (today - filed).days
        mapping = (
            row["ref_mapping"] == "verified_receipt"
            and bool(ref)
            and bool(row["receipt_evidence"].strip())
        )
        due = False
        if response == "received":
            status = "מענה מתועד"
        elif response == "unknown":
            status = "מענה לא נבדק"
        elif checked != today:
            status = "נדרשת בדיקת מענה עדכנית"
        elif age < REMINDER_DAYS:
            status = "טרם חלף שבוע"
        elif reminded and reminded >= checked:
            status = "כבר נשלחה תזכורת לפי הבדיקה הנוכחית"
        elif not mapping:
            status = "חלף שבוע; שיוך הפנייה דורש אימות"
        else:
            status = "מועמד לתזכורת; נדרש אישור"
            due = True
        reports.append((row, status, age, mapping, due))
    reports.sort(key=lambda item: (item[0]["filed_date"], item[0]["folder_id"]))
    lines = [
        f"# טיוטת מצב פרטית ליום {as_of}",
        "",
        "לבדיקה בלבד. אין שליחה אוטומטית. נמענים ונוסח סופי דורשים אישור.",
        "מועמד לתזכורת רק אחרי שבעה ימים, בדיקת מענה עדכנית ושיוך פנייה מאומת ביומן.",
        "היעדר שדה מענה אינו הוכחה שלא התקבל מענה. אישור פתיחת פנייה אינו מענה לטיפול.",
        "",
        "| מזהה דיווח | תאריך | רחוב | לוחית (פרטי) | מספר פנייה | שיוך | X | Facebook | מענה | מצב |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    labels = {
        "unknown": "לא ידוע",
        "no_reply": "ללא מענה לפי בדיקה",
        "received": "מענה מתועד",
    }
    for row, status, age, mapping, due in reports:
        values = (
            row["folder_id"],
            row["filed_date"],
            row["street"],
            row["plate"] or "חסר",
            row["municipal_ref"] or "חסר",
            "מאומת ביומן" if mapping else "לא מאומת",
            row["x_post_url"] or "חסר",
            row["facebook_post_url"] or "חסר",
            labels[row["municipal_response"] or "unknown"],
            status,
        )
        lines.append("| " + " | ".join(_safe(v) for v in values) + " |")
    candidates = [(row, age) for row, status, age, mapping, due in reports if due]
    lines += [
        "",
        f"## טיוטות תזכורת: {len(candidates)}",
        "",
        "יש לבדוק שוב את המענה ואת מקור שיוך הפנייה לפני שליחה; הקוד קורא סימונים, לא מאמת מקורות.",
    ]
    for row, age in candidates:
        lines += [
            "",
            f"### פנייה {row['municipal_ref']}",
            "",
            f"שלום, אבקש עדכון על פנייה {row['municipal_ref']} שנפתחה בתאריך {row['filed_date']}. תודה.",
        ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    parser.add_argument(
        "--as-of", required=True, help="Explicit local review date, YYYY-MM-DD"
    )
    args = parser.parse_args(argv)
    try:
        with args.ledger.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not REQUIRED.issubset(reader.fieldnames or []):
                raise ValueError("Missing ledger columns")
            draft = status_draft(reader, args.as_of)
    except (OSError, UnicodeError, ValueError, csv.Error):
        print(
            "Status draft blocked: check private ledger and review date; no output generated.",
            file=sys.stderr,
        )
        return 1
    print(draft, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
