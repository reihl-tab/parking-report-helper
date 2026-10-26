"""Captured municipal subjects and per-report selection. No network or inference."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

DEFAULT_SUBJECT = "1145"


def load_catalog() -> dict[str, list[dict[str, Any]]]:
    """Preserve every name/path, including repeated subject codes."""
    data = json.loads(
        files("parking_report")
        .joinpath("configs/subjects.json")
        .read_text(encoding="utf-8")
    )
    if data.get("version") != 1 or not isinstance(data.get("subjects"), list):
        raise ValueError("Invalid subject catalog")
    result: dict[str, list[dict[str, Any]]] = {}
    for row in data["subjects"]:
        code = row.get("code")
        if (
            not isinstance(code, str)
            or not code.isascii()
            or not code.isdigit()
            or not isinstance(row.get("name"), str)
            or not row["name"].strip()
            or not isinstance(row.get("path"), list)
            or not row["path"]
            or not all(isinstance(p, str) and p.strip() for p in row["path"])
        ):
            raise ValueError("Invalid subject catalog entry")
        result.setdefault(code, []).append(row)
    return result


def select_subject(record: dict[str, Any]) -> str:
    """Only report data chooses a code; omitted selection keeps the old default."""
    selection = record.get("municipal_subject")
    if selection is None:
        return DEFAULT_SUBJECT
    if not isinstance(selection, dict):
        raise ValueError("municipal_subject must be an object")  # noqa: TRY004 - malformed input data; callers catch ValueError or broadly
    code = selection.get("code")
    if not isinstance(code, str) or code not in load_catalog():
        raise ValueError("Unknown subject code; use a string from the captured catalog")
    return code


def is_parking_subject(code: str) -> bool:
    return any(row["path"][0] == "חניה אסורה" for row in load_catalog()[code])


def validate_owner_choice(record: dict[str, Any], code: str) -> None:
    """Require the recorded choice to match the true event, never change its type.

    This record is an audit field, not proof of authorization. The operator must
    verify the owner's original approval of text, subject, photo and address.
    """
    if select_subject(record) != code:
        raise ValueError("Payload and report subject differ")
    category = record.get("violation", {}).get("category")
    choice = record.get("municipal_subject")
    if choice is None and category == "sidewalk_parking" and code == DEFAULT_SUBJECT:
        return  # Existing sidewalk reports keep their original gate.
    if (
        not isinstance(choice, dict)
        or choice.get("owner_choice") is not True
        or not isinstance(category, str)
        or not category.strip()
        or choice.get("event_type") != category
    ):
        raise ValueError("Record the owner's exact subject choice and true event type")
    if (
        not isinstance(record.get("approved_description"), str)
        or not record["approved_description"].strip()
    ):
        raise ValueError(
            "Explicit subject choices require the exact reviewed description"
        )
