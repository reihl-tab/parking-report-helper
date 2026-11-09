"""Captured municipal subjects and per-report selection. No network or inference."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from typing import Any

DEFAULT_SUBJECT = "1145"

_MAX_CATEGORY_ROWS = 10000
_MAX_CATEGORY_DEPTH = 10


def flatten_category_tree(nodes: Any) -> list[dict[str, Any]]:
    """Flatten a GetSubjectCategories tree into captured-catalog rows.

    Every node must carry a non-empty categoryName; children, when present,
    must be a list of nodes. Only subject-bearing nodes (subjectCode set)
    produce rows, preserving repeated codes under different parents, exactly
    like the captured catalog. Fails closed on any malformed node.
    """
    if not isinstance(nodes, list):
        raise ValueError("Category tree must be a list of nodes")  # noqa: TRY004 - malformed input data; callers catch ValueError
    rows: list[dict[str, Any]] = []
    for node in nodes:
        _flatten_node(node, [], rows)
    return rows


def _flatten_node(node: Any, path: list[str], rows: list[dict[str, Any]]) -> None:
    if len(path) >= _MAX_CATEGORY_DEPTH:
        raise ValueError("Category tree is too deep")
    if not isinstance(node, dict):
        raise ValueError("Category node must be an object")  # noqa: TRY004 - malformed input data; callers catch ValueError
    name = node.get("categoryName")
    code = node.get("subjectCode")
    children = node.get("children")
    if (
        not isinstance(name, str)
        or not name.strip()
        or (
            code is not None
            and not (isinstance(code, str) and code.isascii() and code.isdigit())
        )
        or (children is not None and not isinstance(children, list))
    ):
        raise ValueError("Invalid category node")
    if code is not None:
        rows.append({"code": code, "name": name, "path": path + [name]})
        if len(rows) > _MAX_CATEGORY_ROWS:
            raise ValueError("Category tree is too large")
    for child in children or []:
        _flatten_node(child, path + [name], rows)


def catalog_fingerprint(rows: list[dict[str, Any]]) -> str:
    """Stable sha256 over sorted (code, name, path) rows; secret-free.

    Comparable between a live flattened tree and the captured catalog, so a
    refresh can detect municipal additions, removals and renames.
    """
    items = sorted(
        json.dumps([row["code"], row["name"], row["path"]], ensure_ascii=False)
        for row in rows
    )
    return hashlib.sha256("\n".join(items).encode("utf-8")).hexdigest()


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


def recorded_path(record: dict[str, Any]) -> list[str] | None:
    """Validated catalog path recorded with an explicit subject choice, or None.

    Codes like 5164 ("אחר") sit under several parents, including "חניה אסורה";
    any() alone marks every such code parking and wrongly demands a plate for
    non-parking issues. The recorded path picks the intended catalog row.
    """
    selection = record.get("municipal_subject")
    if not isinstance(selection, dict) or "path" not in selection:
        return None
    path = selection["path"]
    if (
        not isinstance(path, list)
        or not path
        or not all(isinstance(part, str) and part.strip() for part in path)
    ):
        raise ValueError("municipal_subject.path must be a non-empty list of strings")
    code = select_subject(record)
    if path not in [row["path"] for row in load_catalog()[code]]:
        raise ValueError("Recorded subject path is not in the captured catalog")
    return path


def is_parking_selection(record: dict[str, Any]) -> bool:
    """Parking determination for one record: the recorded path wins when given."""
    path = recorded_path(record)
    if path is not None:
        return path[0] == "חניה אסורה"
    return is_parking_subject(select_subject(record))


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
    recorded_path(record)  # Validate a recorded catalog path, when present.
    if (
        not isinstance(record.get("approved_description"), str)
        or not record["approved_description"].strip()
    ):
        raise ValueError(
            "Explicit subject choices require the exact reviewed description"
        )
