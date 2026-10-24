"""Read intake report folders (one folder per report) and map them to Car records.

Layout, one subfolder per report::

    <root>/2026-10-05_1005_ab12/report.json
    <root>/2026-10-05_1005_ab12/photo.jpg

Read-only. Records that are not ready are skipped with a reason and never guessed.
See docs/INTAKE.md for the record format.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
RECORD_NAME = "report.json"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".gif", ".tif", ".tiff"}


@dataclass(frozen=True)
class Intake:
    """Result of reading an intake root."""

    cars: list[dict[str, str]]
    photos: list[str]  # photo path per car, "" when the file is missing
    skipped: list[tuple[str, str]]  # (folder name, reason)


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def record_to_car(record: object) -> dict[str, str]:
    """Map one record to the car mapping used by report.py. Raises ValueError if not usable."""
    if not isinstance(record, dict):
        raise ValueError("record must be a JSON object")
    if record.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version {record.get('schema_version')!r}")
    status = _text(record.get("status"))
    violation = record.get("violation")
    violation = violation if isinstance(violation, dict) else {}
    if status != "ready":
        question = _text(violation.get("question"))
        raise ValueError(
            f"status is {status or 'missing'!r}" + (f": {question}" if question else "")
        )
    if violation.get("needs_clarification"):
        raise ValueError(
            "violation needs clarification: " + _text(violation.get("question"))
        )
    address = record.get("address")
    address = address if isinstance(address, dict) else {}
    street = _text(address.get("street"))
    if not street:
        raise ValueError("address.street is missing")
    number = _text(address.get("house_number"))
    if not number:
        raise ValueError(
            "address.house_number is missing; confirm a number or research the nearest numbered address and mark it approximate"
        )
    from .subjects import select_subject, is_parking_subject

    parking = is_parking_subject(select_subject(record))
    if not parking and not _text(violation.get("description")):
        raise ValueError("A municipal issue needs an observed description")
    car = {
        "plate": _text(record.get("plate")),
        "street": f"{street} {number}".strip(),
        "datetime": _text(record.get("captured_at")),
        "car_type": _text(record.get("car_type")),
        "notes": _text(violation.get("description")),
    }
    if not parking:
        car["draft_kind"] = "municipal_issue"
        car["event_type"] = _text(violation.get("category"))
    location_description = _text(record.get("location_description"))
    if location_description:
        car["notes"] += f". תיאור המקום: {location_description}"
    # Coordinates are never put in report text (owner decision 2026-10-08).
    from .registry import describe_he

    registry_text = describe_he(record.get("registry")) if parking else ""
    if registry_text:
        car["registry_text"] = registry_text
    if address.get("approximate"):
        car["notes"] += ". הכתובת היא קירוב לפי הבניין הממוספר הקרוב לנקודת הצילום"
    for key in ("plate", "datetime") if parking else ("datetime",):
        if not car[key]:
            raise ValueError(f"{key} is missing")
    return car


def _find_photo(folder: Path, record: object) -> str:
    name = Path(_text(record.get("photo")) if isinstance(record, dict) else "").name
    if name and (folder / name).is_file():
        return str(folder / name)
    for path in sorted(folder.iterdir()):
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            return str(path)
    return ""


def read_intake(root: str | Path) -> Intake:
    """Read every report folder under root, oldest first. Never writes anything."""
    base = Path(root)
    if not base.is_dir():
        raise ValueError(f"intake folder not found: {root}")
    cars: list[dict[str, str]] = []
    photos: list[str] = []
    skipped: list[tuple[str, str]] = []
    for folder in sorted(p for p in base.iterdir() if p.is_dir()):
        path = folder / RECORD_NAME
        if not path.is_file():
            skipped.append((folder.name, f"no {RECORD_NAME}"))
            continue
        try:
            record: Any = json.loads(path.read_text(encoding="utf-8"))
            car = record_to_car(record)
        except (OSError, UnicodeError, ValueError) as error:
            skipped.append((folder.name, str(error)))
            continue
        cars.append(car)
        photos.append(_find_photo(folder, record))
    return Intake(cars, photos, skipped)
