"""Local statistics over municipal 106 case-listing exports (issue #210).

Everything here is offline: input files never leave the machine, no network
calls, no telemetry. Input is the ``reports_raw.json`` shape produced by the
municipality export: ``{"incidents": [...], ...}``. Only synthetic fixtures
are used in tests; real exports must never be committed (see the issue).

Aggregation notes:

- Quantiles (median/p25/p75) use the nearest-rank method on the sorted
  values: the value at index ``ceil(fraction * n) - 1``. With few data
  points this intentionally returns an observed value, never an
  interpolation.
- An incident whose ``createdon`` is missing or unparseable is counted in
  ``total_incidents`` and in every date-independent aggregate, but in no
  month: it cannot be placed on the timeline, and guessing a month would
  be worse than dropping it there.
- Hotspots (street + house number) carry a minimum-count floor: a spot
  reported fewer than ``min_hotspot_count`` times (default 3) never
  appears in any output. The floor exists so a published aggregate can
  never single out a one-off address (approved heat-map design, 1.11).
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable

NO_STREET = "(ללא רחוב)"
UNKNOWN = "(לא ידוע)"

# Closure-time buckets in days: <1h, 1-8h, 8-24h, 1-7d, >7d.
CLOSURE_BUCKETS = (
    ("עד שעה", 0.0, 1 / 24),
    ("שעה עד 8 שעות", 1 / 24, 8 / 24),
    ("8 שעות עד יממה", 8 / 24, 1.0),
    ("יממה עד שבוע", 1.0, 7.0),
    ("מעל שבוע", 7.0, None),
)


class StatsError(ValueError):
    """Raised for malformed export files or unsupported shapes."""


def parse_datetime(value: Any) -> dt.datetime | None:
    """Parse the export's ISO-ish datetimes; tolerate None/junk/timezones."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return parsed


def _read_export(path: Path) -> list[dict]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StatsError(f"cannot read export {path}: {exc}") from exc
    if isinstance(raw, dict):
        incidents = raw.get("incidents")
    elif isinstance(raw, list):
        incidents = raw
    else:
        incidents = None
    if not isinstance(incidents, list) or any(
        not isinstance(item, dict) for item in incidents
    ):
        raise StatsError(f"{path}: expected an object with an 'incidents' list")
    return incidents


def load_incidents(paths: Iterable[str | Path]) -> tuple[list[dict], int]:
    """Load one or more exports, deduplicated by case_number (first wins).

    Returns (incidents, duplicates_skipped). Incidents without a usable
    case_number are kept (they cannot collide) and counted separately.
    """
    merged: list[dict] = []
    seen: set[str] = set()
    duplicates = 0
    for path_like in paths:
        path = Path(path_like)
        for incident in _read_export(path):
            case_number = incident.get("case_number")
            if isinstance(case_number, str) and case_number.strip():
                key = case_number.strip()
                if key in seen:
                    duplicates += 1
                    continue
                seen.add(key)
            merged.append(incident)
    return merged, duplicates


def _text(incident: dict, field: str, fallback: str = UNKNOWN) -> str:
    value = incident.get(field)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _count(items: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _quantile(sorted_values: list[float], fraction: float) -> float:
    """Nearest-rank: value at index ceil(fraction * n) - 1."""
    if not sorted_values:
        return 0.0
    index = max(0, math.ceil(fraction * len(sorted_values)) - 1)
    return sorted_values[index]


def closure_days(incident: dict) -> float | None:
    """Days from createdon to ClosedDateIncident, or None when not closed."""
    created = parse_datetime(incident.get("createdon"))
    closed = parse_datetime(incident.get("ClosedDateIncident"))
    if created is None or closed is None:
        return None
    return max(0.0, (closed - created).total_seconds() / 86400)


def _closure_buckets(durations: list[float]) -> dict[str, int]:
    counts = {name: 0 for name, _, _ in CLOSURE_BUCKETS}
    for days in durations:
        for name, low, high in CLOSURE_BUCKETS:
            if days >= low and (high is None or days < high):
                counts[name] += 1
                break
    return counts


def _category_aggregates(
    incidents: list[dict],
) -> tuple[dict[str, int], dict[str, list[float]]]:
    counts: dict[str, int] = {}
    closures: dict[str, list[float]] = {}
    for incident in incidents:
        category = _text(incident, "subjectname")
        counts[category] = counts.get(category, 0) + 1
        days = closure_days(incident)
        if days is not None:
            closures.setdefault(category, []).append(days)
    ordered = dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    return ordered, closures


def _hotspots(incidents: list[dict], limit: int, min_count: int) -> dict[str, int]:
    spots: dict[str, int] = {}
    for incident in incidents:
        street = incident.get("streetname")
        if not (isinstance(street, str) and street.strip()):
            continue  # streetless incidents are by_street's "(ללא רחוב)", not hotspots
        street = street.strip()
        number = incident.get("housenumber")
        number = str(number).strip() if number not in (None, "") else ""
        spot = f"{street} {number}".strip()
        spots[spot] = spots.get(spot, 0) + 1
    ordered = sorted(spots.items(), key=lambda kv: (-kv[1], kv[0]))
    return {spot: count for spot, count in ordered[:limit] if count >= min_count}


def _by_neighborhood(
    incidents: list[dict], neighborhoods: dict[str, str] | None
) -> dict[str, int] | None:
    if neighborhoods is None:
        return None
    counts: dict[str, int] = {}
    for incident in incidents:
        street = _text(incident, "streetname", NO_STREET)
        area = neighborhoods.get(street, "(אחר)")
        counts[area] = counts.get(area, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


_PLATE_FORMS = (
    re.compile(r"^[0-9]{7}$"),
    re.compile(r"^[0-9]{8}$"),
    re.compile(r"^[0-9]{2}-[0-9]{3}-[0-9]{2}$"),
    re.compile(r"^[0-9]{3}-[0-9]{2}-[0-9]{3}$"),
)


def plate_digits(raw: str) -> str | None:
    """Digits of a standard Israeli plate, or None for anything else.

    Only pure 7/8-digit numbers, optionally dashed in the standard
    positions, are publishable; free text, Hebrew-letter formats,
    bidi/control characters and formula prefixes are all rejected.
    """
    text = raw.strip()
    for pattern in _PLATE_FORMS:
        if pattern.fullmatch(text):
            return text.replace("-", "")
    return None


def display_plate(digits: str) -> str:
    """Canonical dashed display form for 7/8-digit plates."""
    if len(digits) == 7:
        return f"{digits[:2]}-{digits[2:5]}-{digits[5:]}"
    if len(digits) == 8:
        return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
    return digits


def _find(parent: list[int], i: int) -> int:
    while parent[i] != i:
        parent[i] = parent[parent[i]]
        i = parent[i]
    return i


def _union(parent: list[int], a: int, b: int) -> None:
    ra, rb = _find(parent, a), _find(parent, b)
    if ra != rb:
        parent[ra] = rb


def _report_groups(rows: list[dict[str, str | None]]) -> int:
    """Distinct reports: rows sharing ANY identifier are the same report.

    Union-find over folder_id and municipal_ref (a report may first be
    recorded with only a folder_id and later gain its municipal_ref).
    Callers must exclude rows lacking both identifiers before calling.
    """
    parent = list(range(len(rows)))
    by_folder: dict[str, int] = {}
    by_ref: dict[str, int] = {}
    for i, row in enumerate(rows):
        folder = (row.get("folder_id") or "").strip()
        ref = (row.get("municipal_ref") or "").strip()
        if folder:
            if folder in by_folder:
                _union(parent, i, by_folder[folder])
            else:
                by_folder[folder] = i
        if ref:
            if ref in by_ref:
                _union(parent, i, by_ref[ref])
            else:
                by_ref[ref] = i
    return len({_find(parent, i) for i in range(len(rows))})


def _valid_filed_date(raw: str) -> bool:
    """A real ISO calendar date (rejects blanks and 2026-02-30)."""
    text = raw.strip()
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
        return False
    try:
        dt.date.fromisoformat(text)
    except ValueError:
        return False
    return True


def plates_summary(
    ledger_path: str | Path, *, min_count: int = 3
) -> tuple[list[dict[str, Any]], int]:
    """Repeat-plate aggregate from the private filing ledger.

    Returns (rows, skipped). The ledger never leaves the machine; only
    the thresholded aggregate (plate, count) may be published, and only
    canonical dashed standard plates appear in it. Reports are
    deduplicated by shared identifiers (see _report_groups) so a case
    recorded twice does not inflate a plate past the threshold. Rows
    excluded from the counts (counted in ``skipped``):

    - no plate, or a plate that is not a standard 7/8-digit number
      (optionally dashed in the standard positions);
    - no valid filed_date;
    - no identifier at all (neither folder_id nor municipal_ref) - an
      unidentifiable copy of an identified report must not be able to
      push a plate past the threshold.
    """
    by_plate: dict[str, list[dict[str, str | None]]] = {}
    skipped = 0
    try:
        with open(ledger_path, encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            header = set(reader.fieldnames or ())
            missing = {"plate", "filed_date"} - header
            if missing:
                raise StatsError(
                    f"ledger {ledger_path}: missing column(s) {sorted(missing)}"
                )
            for row in reader:
                digits = plate_digits(row.get("plate") or "")
                if digits is None or not _valid_filed_date(row.get("filed_date") or ""):
                    skipped += 1
                    continue
                if (
                    not (row.get("folder_id") or "").strip()
                    and not (row.get("municipal_ref") or "").strip()
                ):
                    skipped += 1
                    continue
                by_plate.setdefault(digits, []).append(row)
    except csv.Error as exc:
        raise StatsError(f"ledger {ledger_path}: malformed CSV: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise StatsError(f"ledger {ledger_path}: not valid UTF-8: {exc}") from exc
    rows: list[dict[str, Any]] = []
    for digits, plate_rows in by_plate.items():
        count = _report_groups(plate_rows)
        if count < min_count:
            continue
        rows.append({"plate": display_plate(digits), "reports": count})
    rows.sort(key=lambda r: (-r["reports"], r["plate"]))
    return rows, skipped


def summarize(
    incidents: list[dict],
    *,
    top_streets: int = 20,
    top_hotspots: int = 10,
    min_hotspot_count: int = 3,
    weeks: int = 12,
    neighborhoods: dict[str, str] | None = None,
) -> dict:
    """Aggregate incidents into the summary document consumed by the CLI."""
    created_dates = [
        parsed
        for parsed in (parse_datetime(i.get("createdon")) for i in incidents)
        if parsed is not None
    ]
    months = [f"{d.year:04d}-{d.month:02d}" for d in created_dates]
    iso_weeks = [
        f"{iso.year:04d}-W{iso.week:02d}"
        for iso in (d.isocalendar() for d in created_dates)
    ]

    durations = [
        days for days in (closure_days(i) for i in incidents) if days is not None
    ]
    durations.sort()
    bucket_counts = _closure_buckets(durations)
    by_category, closure_by_category = _category_aggregates(incidents)

    summary = {
        "schema_version": 1,
        "total_incidents": len(incidents),
        "date_range": {
            "first": min(created_dates).date().isoformat() if created_dates else None,
            "last": max(created_dates).date().isoformat() if created_dates else None,
        },
        "by_month": dict(sorted(_count(months).items())),
        "by_week": dict(list(sorted(_count(iso_weeks).items()))[-weeks:]),
        "hotspots": _hotspots(incidents, top_hotspots, min_hotspot_count),
        "by_neighborhood": _by_neighborhood(incidents, neighborhoods),
        "by_category": by_category,
        "by_street": dict(
            list(_count(_text(i, "streetname", NO_STREET) for i in incidents).items())[
                :top_streets
            ]
        ),
        "by_status": _count(_text(i, "statusname") for i in incidents),
        "closure": {
            "closed_count": len(durations),
            "median_days": round(_quantile(durations, 0.5), 3),
            "p25_days": round(_quantile(durations, 0.25), 3),
            "p75_days": round(_quantile(durations, 0.75), 3),
            "max_days": round(durations[-1], 3) if durations else 0.0,
            "buckets": bucket_counts,
            "median_days_by_category": {
                category: round(_quantile(sorted(values), 0.5), 3)
                for category, values in sorted(
                    closure_by_category.items(),
                    key=lambda kv: (-len(kv[1]), kv[0]),
                )
            },
        },
    }
    return summary


def union_with_fetched(
    archived: list[dict], fetched: list[dict]
) -> tuple[list[dict], int]:
    """Union archived export rows with live-fetched rows by case_number.

    Fetched rows win on overlap (they carry the current status); archived
    rows without a case_number cannot collide and are kept as-is, fetched
    rows without one raise StatsError (a live row with no identity would
    silently duplicate its archived copy). Returns (merged, overlaps).
    """
    keys: dict[str, int] = {}
    merged: list[dict] = []
    for incident in archived:
        case_number = incident.get("case_number")
        if isinstance(case_number, str) and case_number.strip():
            keys[case_number.strip()] = len(merged)
        merged.append(incident)
    overlaps = 0
    for incident in fetched:
        case_number = incident.get("case_number")
        if not isinstance(case_number, str) or not case_number.strip():
            raise StatsError("fetched incident without a case_number")
        key = case_number.strip()
        if key in keys:
            merged[keys[key]] = incident
            overlaps += 1
        else:
            keys[key] = len(merged)
            merged.append(incident)
    return merged, overlaps
