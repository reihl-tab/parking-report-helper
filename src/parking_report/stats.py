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
"""

from __future__ import annotations

import datetime as dt
import json
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
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, int(fraction * len(sorted_values)))
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


def summarize(incidents: list[dict], *, top_streets: int = 20) -> dict:
    """Aggregate incidents into the summary document consumed by the CLI."""
    created_dates = [
        parsed
        for parsed in (parse_datetime(i.get("createdon")) for i in incidents)
        if parsed is not None
    ]
    months = [f"{d.year:04d}-{d.month:02d}" for d in created_dates]

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
                    key=lambda kv: -len(kv[1]),
                )
            },
        },
    }
    return summary
