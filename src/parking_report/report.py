"""Report formatting. Pure functions, no I/O."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

REQUIRED = ("plate", "street", "datetime")


def format_datetime(value: object) -> str:
    """Accept 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DDTHH:MM'; return 'D.M.YYYY, HH:MM'."""
    text = str(value).strip().replace("T", " ")
    try:
        dt = datetime.strptime(text, "%Y-%m-%d %H:%M")
    except ValueError:
        raise ValueError(f"bad datetime {value!r}, expected YYYY-MM-DD HH:MM") from None
    return f"{dt.day}.{dt.month}.{dt.year}, {dt:%H:%M}"


@dataclass(frozen=True)
class Car:
    """One reported vehicle."""

    plate: str
    street: str
    datetime: str
    car_type: str = ""
    notes: str = ""
    draft_kind: str = "parking"
    event_type: str = ""
    registry_text: str = ""

    @classmethod
    def from_mapping(cls, data: object) -> Car:
        if not isinstance(data, Mapping):
            raise ValueError("each car must be a JSON object")
        kind = data.get("draft_kind", "parking")
        if kind not in {"parking", "municipal_issue"}:
            raise ValueError("unknown draft kind")
        required = (
            REQUIRED
            if kind == "parking"
            else ("street", "datetime", "notes", "event_type")
        )
        missing = [
            key
            for key in required
            if not isinstance(data.get(key), str) or not data[key].strip()
        ]
        if missing:
            raise ValueError("missing field(s): " + ", ".join(missing))
        return cls(
            plate=str(data.get("plate") or "").strip(),
            street=data["street"].strip(),
            datetime=data["datetime"],
            car_type=str(data.get("car_type") or "").strip(),
            notes=str(data.get("notes") or "").strip(),
            draft_kind=kind,
            event_type=str(data.get("event_type") or "").strip(),
            registry_text=str(data.get("registry_text") or "").strip(),
        )


def build_report(
    car: Car | Mapping[str, Any] | object,
    city: str = "",
    name: str = "",
    with_photo: bool = True,
) -> str:
    """Build one Hebrew report text. Raises ValueError on invalid input."""
    record = car if isinstance(car, Car) else Car.from_mapping(car)
    place = f"ברחוב {record.street}"
    if city:
        place += f", {city}"
    if record.draft_kind == "municipal_issue":
        parts = [
            f"שלום, אני מדווח על מפגע: {record.notes}.",
            f"מיקום: {place}.",
            f"תאריך ושעה: {format_datetime(record.datetime)}.",
        ]
        if with_photo:
            parts.append("מצורפת תמונה.")
        parts.extend(
            [
                "אבקש לטפל במפגע ולעדכן אותי במספר הפנייה.",
                "תודה" + (f", {name}" if name else ""),
            ]
        )
        return " ".join(parts)
    plate_text = (
        f"{record.plate} ({record.car_type})" if record.car_type else record.plate
    )
    parts = [
        f"שלום, אני מדווח על רכב שחונה {record.notes or 'בחניה אסורה'}.",
        f"מיקום: {place}.",
        f"תאריך ושעה: {format_datetime(record.datetime)}.",
        f"מספר רכב: {plate_text}.",
    ]
    if record.registry_text:
        parts.append(record.registry_text)
    if with_photo:
        parts.append("מצורפת תמונה.")
    parts.append("אבקש לשלוח פקח אכיפת חניה ולעדכן אותי במספר הפנייה.")
    parts.append("תודה" + (f", {name}" if name else ""))
    return " ".join(parts)


def build_reports(cars: object, **kwargs: Any) -> list[str]:
    """Build a report per car; errors name the failing 1-based position."""
    if not isinstance(cars, list):
        raise ValueError("input must be a JSON list of cars")
    reports: list[str] = []
    for index, car in enumerate(cars, start=1):
        try:
            reports.append(build_report(car, **kwargs))
        except ValueError as error:
            raise ValueError(f"car {index}: {error}") from error
    return reports
