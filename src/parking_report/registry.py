"""Government plate lookup. Write only a private result file; never log registry data."""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

BASE = "https://data.gov.il/api/3/action/datastore_search"
VEHICLES = "053cea08-09bc-40ec-8f7a-156f0677aff3"
BADGES = "c8b9f9c8-4612-4068-934f-d4acd2e3c06e"
FIELDS = (
    "mispar_rechev",
    "tozeret_nm",
    "degem_nm",
    "kinuy_mishari",
    "tzeva_rechev",
    "shnat_yitzur",
    "mivchan_acharon_dt",
    "tokef_dt",
)


def query(resource: str, field: str, plate: str) -> dict[str, Any]:
    """One exact filtered GET, no retries; no bulk data or owner/chassis lookup."""
    parameters = urllib.parse.urlencode(
        {
            "resource_id": resource,
            "filters": json.dumps({field: int(plate)}),
            "limit": 2,
        }
    )
    with urllib.request.urlopen(BASE + "?" + parameters, timeout=30) as response:
        data = json.load(response)
    if data.get("success") is not True or not isinstance(data.get("result"), dict):
        raise ValueError("Government lookup did not confirm success")
    return data["result"]


def lookup(
    plate: str, today: date, fetch: Callable[..., dict[str, Any]] = query
) -> dict[str, Any]:
    """Preserve errors, absence and ambiguity as separate unknown states."""
    plate = re.sub(r"[ -]", "", plate)
    if not re.fullmatch(r"\d{7,8}", plate):
        raise ValueError("Expected a seven/eight digit vehicle plate")
    out: dict[str, Any] = {
        "plate": plate,
        "checked_on": today.isoformat(),
        "checked_at": datetime.now().astimezone().isoformat(),
        "licence_status": "unknown",
        "disability_status": "unknown",
        "sources": {"vehicle": VEHICLES, "disability": BADGES, "api": BASE},
    }
    for resource, field, key in [
        (VEHICLES, "mispar_rechev", "vehicle"),
        (BADGES, "MISPAR RECHEV", "disability"),
    ]:
        try:
            result = fetch(resource, field, plate)
            rows = result.get("records")
            if not isinstance(rows, list):
                raise ValueError("Missing records")
            total = result.get("total")
            if not isinstance(total, int) or total != len(rows):
                raise ValueError("Ambiguous result")
            if total == 0:
                out[key + "_lookup"] = "not_found_in_dataset"
                continue
            if total != 1 or str(rows[0].get(field)) != str(int(plate)):
                raise ValueError("Plate mismatch or multiple records")
            row = rows[0]
            out[key + "_lookup"] = "matched"
            if key == "vehicle":
                out["vehicle"] = {k: row.get(k) for k in FIELDS}
                expiry = date.fromisoformat(str(row.get("tokef_dt"))[:10])
                out["licence_status"] = (
                    "valid_on_check_date"
                    if expiry >= today
                    else "expired_on_check_date"
                )
            else:
                out["disability_status"] = "listed_in_dataset"
                out["disability"] = {
                    k: row.get(k) for k in ("TAARICH HAFAKAT TAG", "SUG TAV")
                }
        except Exception:
            out[key + "_lookup"] = "unverified"
    return out


def _he_date(value: Any) -> str:
    """'2026-10-08T00:00:00' -> '8.10.2026'; empty string when unusable."""
    text = str(value).strip()
    if re.fullmatch(r"\d{8}", text):  # the tag dataset stores 20260101
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        d = date.fromisoformat(text[:10])
    except ValueError:
        return ""
    return f"{d.day}.{d.month}.{d.year}"


def describe_he(result: Any) -> str:
    """Hebrew sentences for a municipal report from a lookup() result.

    Only make/model/colour, licence validity, last test date and disability tag
    status are used. Unknown, unmatched or failed lookups are omitted, never
    reported as a finding. Returns '' when nothing can be stated."""
    if not isinstance(result, dict) or not result.get("plate"):
        return ""
    checked = _he_date(result.get("checked_on"))
    parts = [
        "בדיקה במאגרי משרד התחבורה" + (f" (נכון ל-{checked})" if checked else "") + ":"
    ]
    vehicle_lookup = result.get("vehicle_lookup")
    vehicle = result.get("vehicle") if isinstance(result.get("vehicle"), dict) else {}
    if vehicle_lookup == "matched":
        name = " ".join(
            str(vehicle[k]).strip()
            for k in ("tozeret_nm", "degem_nm")
            if vehicle.get(k)
        )
        if vehicle.get("kinuy_mishari"):
            name += f" ({str(vehicle['kinuy_mishari']).strip()})"
        if name:
            parts.append(
                f"רכב מסוג {name}"
                + (
                    f", צבע {str(vehicle['tzeva_rechev']).strip()}."
                    if vehicle.get("tzeva_rechev")
                    else "."
                )
            )
        expiry = _he_date(vehicle.get("tokef_dt"))
        status = result.get("licence_status")
        if expiry and status == "valid_on_check_date":
            parts.append(f"תוקף רישיון הרכב עד {expiry} (בתוקף).")
        elif expiry and status == "expired_on_check_date":
            parts.append(f"תוקף רישיון הרכב פג ב-{expiry}; אין לרכב רישיון בתוקף.")
        else:
            parts.append("תוקף הרישיון לא אומת.")
        test = _he_date(vehicle.get("mivchan_acharon_dt"))
        if test:
            parts.append(f"מבחן רישוי אחרון: {test}.")
    # No match or failed lookup: say nothing about licence/test (unknown is not a finding).
    disability_lookup = result.get("disability_lookup")
    if disability_lookup == "matched":
        tag = (
            result.get("disability")
            if isinstance(result.get("disability"), dict)
            else {}
        )
        text = "לרכב רשום תג נכה במאגר תגי הנכים"
        kind = tag.get("SUG TAV")
        if kind not in (None, ""):
            text += f" (קוד סוג תג: {str(kind).strip()})"
        issued = _he_date(tag.get("TAARICH HAFAKAT TAG"))
        if issued:
            text += f", הונפק ב-{issued}"
        parts.append(text + ".")
    elif disability_lookup == "not_found_in_dataset":
        parts.append("לא נמצא לרכב תג נכה במאגר תגי הנכים.")
    # Failed disability lookup: omitted as well.
    return " ".join(parts) if len(parts) > 1 else ""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("record", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    try:
        record = json.loads(args.record.read_text(encoding="utf-8"))
        result = lookup(record["plate"], date.today())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        print("Registry check saved privately; review unknown results before use.")
        return 0
    except Exception:
        print("Registry check blocked; no private values printed.")
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
