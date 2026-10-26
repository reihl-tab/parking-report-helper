"""Per-municipality submission config and field values. Pure functions plus config loading."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import Any

from .report import Car, build_report

_ADDRESS = re.compile(r"^(?P<street>.*?)\s+(?P<number>\d+[א-תA-Za-z]?)$")
MISSING = "<חסר>"


def load_config(name_or_path: str) -> dict[str, Any]:
    """Load a bundled config by name (e.g. 'jerusalem') or a JSON file by path."""
    path = Path(name_or_path)
    if path.suffix == ".json" and path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        if not re.fullmatch(r"[a-z0-9_-]+", name_or_path):
            raise ValueError(f"unknown municipality {name_or_path!r}")
        try:
            text = (
                resources.files("parking_report")
                .joinpath("configs", f"{name_or_path}.json")
                .read_text(encoding="utf-8")
            )
        except (FileNotFoundError, OSError):
            raise ValueError(f"unknown municipality {name_or_path!r}") from None
    config = json.loads(text)
    if not isinstance(config, dict) or not isinstance(config.get("fields"), list):
        raise ValueError("invalid municipality config: 'fields' list required")  # noqa: TRY004 - malformed input data; callers catch ValueError
    return config


def split_address(street: str) -> tuple[str, str]:
    """Split 'Street 12' into ('Street', '12'). No trailing number: (street, '')."""
    match = _ADDRESS.match(street.strip())
    if match:
        return match["street"].strip(), match["number"]
    return street.strip(), ""


def build_submission(
    car: Car | Mapping[str, Any],
    config: Mapping[str, Any],
    reporter: Mapping[str, Any] | None = None,
    with_photo: bool = True,
) -> list[tuple[str, str, str]]:
    """Return (key, label, value) per form field, in form order.

    Required values that are not available are set to MISSING.
    """
    record = car if isinstance(car, Car) else Car.from_mapping(car)
    reporter = reporter or {}
    street, number = split_address(record.street)
    derived = {
        "city": str(config.get("city", "")),
        "street": street,
        "house_number": number,
        "description": build_report(record, city="", with_photo=with_photo),
    }
    rows: list[tuple[str, str, str]] = []
    for field in config["fields"]:
        key = field["key"]
        if field.get("source") == "reporter":
            value = str(reporter.get(key) or "").strip()
        else:
            value = derived.get(key, "")
        if not value and field.get("required"):
            value = MISSING
        rows.append((key, field["label"], value))
    return rows


def build_submissions(
    cars: object,
    config: Mapping[str, Any],
    reporter: Mapping[str, Any] | None = None,
    with_photo: bool = True,
) -> list[list[tuple[str, str, str]]]:
    """Build the form rows for every car; errors name the failing 1-based position."""
    if not isinstance(cars, list):
        raise ValueError("input must be a JSON list of cars")  # noqa: TRY004 - malformed input data; callers catch ValueError
    submissions = []
    for index, car in enumerate(cars, start=1):
        try:
            submissions.append(
                build_submission(Car.from_mapping(car), config, reporter, with_photo)
            )
        except ValueError as error:
            raise ValueError(f"car {index}: {error}") from error
    return submissions


def format_submission(rows: list[tuple[str, str, str]]) -> str:
    return "\n".join(f"{label}: {value}" for _, label, value in rows)


def submission_dict(rows: list[tuple[str, str, str]]) -> dict[str, str]:
    return {key: value for key, _, value in rows}
