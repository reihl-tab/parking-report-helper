"""Validate an explicit shared-photo manifest (#271); never infer permission.

One unchanged original photo may show two distinct cars. Each car keeps its
own intake, digest, receipt and state. The manifest is an audit record bound
into each member's digest; a live member also needs
``--approved-shared-photo-sha256``. Plates stay in the private record:
``group_fingerprint`` leaves them out and nothing here logs or prints.

Because the ledger is plate-free, a member's reservation also reads its
sibling's record, assumed to sit beside it as ``<parent>/<sibling ID>``, and
refuses unless both manifests are byte-identical (fail-closed).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from .stats import plate_digits

GROUP_SIZE = 2  # Exactly two cars per original photo (maintainer decision B1).

_FIELDS = {"intake_ids", "plates", "photo_sha256", "authority_reference"}
_INTAKE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}")
_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_:/.-]{0,199}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _is_intake_id(value: str) -> bool:
    return _INTAKE_ID.fullmatch(value) is not None


def _is_normalized_plate(value: str) -> bool:
    return plate_digits(value) == value


def _members(value: Any, check: Any, what: str) -> list[str]:
    """Exactly GROUP_SIZE distinct strings that pass check."""
    if (
        not isinstance(value, list)
        or len(value) != GROUP_SIZE
        or not all(isinstance(item, str) and check(item) for item in value)
    ):
        raise ValueError(f"Invalid shared-photo {what}")
    if len(set(value)) != GROUP_SIZE:
        raise ValueError(f"Shared-photo {what} must be distinct")
    return list(value)


def _text(value: Any, pattern: re.Pattern[str], what: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"Invalid shared-photo {what}")
    return value


def validate_shared_photo(value: Any) -> dict[str, Any]:
    """Return the exact validated manifest; reject partial or unknown fields."""
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise ValueError("Shared-photo manifest must contain the exact required fields")
    intake_ids = _members(value["intake_ids"], _is_intake_id, "intake IDs")
    if intake_ids != sorted(intake_ids):
        raise ValueError("Shared-photo intake IDs must be sorted")
    return {
        "intake_ids": intake_ids,
        "plates": _members(value["plates"], _is_normalized_plate, "plates"),
        "photo_sha256": _text(value["photo_sha256"], _SHA256, "photo hash"),
        "authority_reference": _text(
            value["authority_reference"], _REFERENCE, "authority reference"
        ),
    }


def member_manifest(
    record: dict[str, Any], intake_id: str, photo_hash: str
) -> dict[str, Any]:
    """Validate record's manifest for this intake and its actual original photo."""
    if "followup" in record:
        raise ValueError("A follow-up cannot also be a shared-photo member")
    manifest = validate_shared_photo(record["shared_photo"])
    if intake_id not in manifest["intake_ids"]:
        raise ValueError("Intake is not named by its shared-photo manifest")
    if manifest["photo_sha256"] != photo_hash:
        raise ValueError("Shared-photo manifest names another original photo")
    plate = record.get("plate")
    slot = manifest["intake_ids"].index(intake_id)
    if not isinstance(plate, str) or plate_digits(plate) != manifest["plates"][slot]:
        raise ValueError("Intake plate does not match its shared-photo entry")
    return manifest


def group_fingerprint(manifest: dict[str, Any]) -> str:
    """Plate-free group identity for the ledger: IDs, photo and authority only."""
    group = {key: manifest[key] for key in _FIELDS - {"plates"}}
    canonical = json.dumps(group, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def canonical_manifest(manifest: dict[str, Any]) -> bytes:
    """Canonical bytes of a validated manifest: every field, plates included."""
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()


def sibling_record_path(folder: Path, manifest: dict[str, Any]) -> Path:
    """<folder>.parent/<sibling intake ID>/report.json, ID an opaque folder name."""
    others = [i for i in manifest["intake_ids"] if i != folder.name]
    if len(others) != 1 or not _is_intake_id(others[0]):
        raise ValueError("Shared-photo sibling must be an opaque intake folder name")
    return folder.parent / others[0] / "report.json"


def _read_regular(path: Path) -> str:
    """Read path only if it is a regular file; never block on a FIFO/device."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise OSError("not a regular file")
        return handle.read().decode("utf-8")


def _sibling_manifest(path: Path) -> Any:
    try:
        record = json.loads(_read_regular(path))
    except (OSError, ValueError):
        raise ValueError(
            "Shared-photo sibling record is missing or unreadable"
        ) from None
    if not isinstance(record, dict) or "shared_photo" not in record:
        raise ValueError("Shared-photo sibling record has no manifest")
    return record["shared_photo"]


def _identical(other: Any, own: dict[str, Any]) -> bool:
    try:
        theirs = validate_shared_photo(other)
    except ValueError:
        return False
    return canonical_manifest(theirs) == canonical_manifest(own)


def check_sibling(folder: Path, manifest: Any) -> None:
    """Fail closed unless the sibling's record carries the identical manifest."""
    own = validate_shared_photo(manifest)
    other = _sibling_manifest(sibling_record_path(folder, own))
    if not _identical(other, own):
        raise ValueError("Shared-photo sibling manifest differs")
