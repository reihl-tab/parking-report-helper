"""Controlled shared-photo extension after a completed intake (#293).

One explicitly approved, separately verified car may be reported from the
unchanged original photo of one completed ordinary vehicle intake. The parent
photo bytes, payload digest, state and the original photo index never change.
The full design contract is docs/SHARED-PHOTO-EXTENSION-DESIGN.md.

Privacy: the durable ledger stays plate-free. The only exception is the
optional private ``photo_vehicle`` index, which stores HMAC guard keys derived
from (original photo hash, normalized plate) under a provisioned key, never a
raw plate. Raw plates, derived keys and the seed stay out of public output,
logs and diagnostics. Plates here are compared only as structured record
fields, never parsed from prose.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .shared_photo import _read_regular_bytes
from .stats import plate_digits

_FIELDS = {
    "parent_id",
    "parent_digest",
    "photo_sha256",
    "parent_plate",
    "plate",
    "authority_reference",
}
_INTAKE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}")
_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_:/.-]{0,199}")
_SHA256 = re.compile(r"[0-9a-f]{64}")

# Optional preserved submission artifacts beside the parent's report.json.
# Neither exists for legacy intakes by default; at least one must corroborate
# the ledger digest before a parent is eligible.
PAYLOAD_NAME = "municipal-payload.json"
DIGEST_FIELD = "submission_digest"


def _text(value: Any, pattern: re.Pattern[str], what: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"Invalid shared-photo extension {what}")
    return value


def _normalized_plate(value: Any, what: str) -> str:
    if not isinstance(value, str) or plate_digits(value) != value:
        raise ValueError(f"Invalid shared-photo extension {what}")
    return value


def validate_extension(value: Any) -> dict[str, str]:
    """Return the exact validated manifest; reject partial or unknown fields."""
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise ValueError(
            "Shared-photo extension manifest must contain the exact required fields"
        )
    manifest = {
        "parent_id": _text(value["parent_id"], _INTAKE_ID, "parent ID"),
        "parent_digest": _text(value["parent_digest"], _SHA256, "parent digest"),
        "photo_sha256": _text(value["photo_sha256"], _SHA256, "photo hash"),
        "parent_plate": _normalized_plate(value["parent_plate"], "parent plate"),
        "plate": _normalized_plate(value["plate"], "plate"),
        "authority_reference": _text(
            value["authority_reference"], _REFERENCE, "authority reference"
        ),
    }
    if manifest["parent_plate"] == manifest["plate"]:
        raise ValueError("Shared-photo extension plates must be distinct")
    return manifest


def extension_manifest(
    record: dict[str, Any], intake_id: str, photo_hash: str
) -> dict[str, str]:
    """Validate a record's extension manifest for its own photo and plate."""
    if "followup" in record or "shared_photo" in record:
        raise ValueError("An extension cannot combine a follow-up or shared photo")
    manifest = validate_extension(record["shared_photo_extension"])
    if manifest["photo_sha256"] != photo_hash:
        raise ValueError("Extension manifest names another original photo")
    plate = record.get("plate")
    if not isinstance(plate, str) or plate_digits(plate) != manifest["plate"]:
        raise ValueError("Intake plate does not match its extension manifest")
    from .subjects import is_parking_subject, select_subject

    if not is_parking_subject(select_subject(record)):
        raise ValueError("A shared-photo extension reports a vehicle")
    return manifest


def extension_relation(manifest: dict[str, str]) -> dict[str, str]:
    """Plate-free ledger relation: parent key, digests and authority only."""
    return {
        "parent": hashlib.sha256(manifest["parent_id"].encode()).hexdigest(),
        "parent_digest": manifest["parent_digest"],
        "photo_sha256": manifest["photo_sha256"],
        "authority_reference": manifest["authority_reference"],
    }


def _read_json_regular(path: Path, what: str) -> dict[str, Any]:
    try:
        data = json.loads(_read_regular_bytes(path).decode("utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise ValueError(f"{what} is missing or unreadable") from None
    if not isinstance(data, dict):
        raise ValueError(f"{what} is not a JSON object")
    return data


def _photo_bytes(folder: Path, record: dict[str, Any]) -> bytes:
    """Raw original photo bytes; the same name and symlink rules as prepare()."""
    name = record.get("photo")
    if not isinstance(name, str) or not name or Path(name).name != name:
        raise ValueError("Intake record must name one local photo")
    path = folder / name
    if path.is_symlink():
        raise ValueError("Photo symlinks are not allowed")
    try:
        return _read_regular_bytes(path)
    except OSError:
        raise ValueError("Intake photo is missing or unreadable") from None


def _parent_folder(folder: Path, manifest: dict[str, str]) -> Path:
    """<folder>.parent/<parent_id>; the fixed sibling layout, no path option."""
    return folder.parent / manifest["parent_id"]


def _check_parent_record(record: dict[str, Any], manifest: dict[str, str]) -> None:
    if any(
        key in record for key in ("followup", "shared_photo", "shared_photo_extension")
    ):
        raise ValueError("Extension parent record is not an ordinary intake")
    from .subjects import is_parking_subject, select_subject

    try:
        code = select_subject(record)
    except ValueError:
        raise ValueError("Extension parent subject is invalid") from None
    if not is_parking_subject(code):
        raise ValueError("Extension parent is not a vehicle intake")
    plate = record.get("plate")
    if not isinstance(plate, str) or plate_digits(plate) != manifest["parent_plate"]:
        raise ValueError("Extension parent plate does not match the manifest")


def _check_photos(
    folder: Path,
    record: dict[str, Any],
    parent: Path,
    parent_record: dict[str, Any],
    manifest: dict[str, str],
) -> None:
    """Both originals carry the declared hash and are byte-identical."""
    own_raw = _photo_bytes(folder, record)
    parent_raw = _photo_bytes(parent, parent_record)
    declared = manifest["photo_sha256"]
    if hashlib.sha256(parent_raw).hexdigest() != declared:
        raise ValueError("Parent original photo does not have the declared hash")
    if hashlib.sha256(own_raw).hexdigest() != declared or own_raw != parent_raw:
        raise ValueError("Extension photo is not the parent's unchanged original")


def check_parent_ledger(manifest: dict[str, str], data: dict[str, Any]) -> None:
    """The ledger parent is complete, ordinary and owns the original photo."""
    parent_key = hashlib.sha256(manifest["parent_id"].encode()).hexdigest()
    entry = data.get("reports", {}).get(parent_key)
    if not isinstance(entry, dict):
        raise ValueError("Extension parent is missing from the private ledger")
    if entry.get("state", {}).get("status") != "complete":
        raise ValueError("Extension parent is not complete")
    if any(key in entry for key in ("followup", "shared_photo", "extension")):
        raise ValueError("Extension parent is not an ordinary ledger entry")
    stored = (entry.get("digest"), entry.get("photo_sha256"))
    if stored != (manifest["parent_digest"], manifest["photo_sha256"]):
        raise ValueError("Extension parent digest or photo differs")
    if data.get("photos", {}).get(manifest["photo_sha256"]) != parent_key:
        raise ValueError("Extension parent does not own the original photo index")


def _corroborate_digest(
    parent: Path,
    parent_record: dict[str, Any],
    manifest: dict[str, str],
    addresses: Path,
    reporter: Any,
) -> None:
    """Preserved artifacts plus a recompute must match the ledger digest.

    The ledger entry is the source of truth for the submission digest, but a
    saved digest string alone never corroborates it. The original payload is
    recomputed through the preparation contract from the parent's record,
    original photo and the original reporter inputs (never printed or
    persisted), and at least one preserved artifact must agree:

    - ``submission_digest`` recorded in the parent's report.json, or
    - the saved submitted payload ``municipal-payload.json``, which must equal
      the recomputed payload in full (description, subject and address
      included).

    The recompute binds the parent's structured plate into the digest, so a
    payload whose plate diverged cannot match; plates are never parsed from
    prose. A municipal case number is optional metadata only: it never
    selects the parent and is never upgraded by an extension approval.
    """
    from .api_cli import prepare

    _, report, _, recomputed = prepare(parent, addresses, reporter)
    if recomputed != manifest["parent_digest"]:
        raise ValueError("Parent record no longer reproduces the submitted digest")
    matched = False
    recorded = parent_record.get(DIGEST_FIELD)
    if recorded is not None:
        if (
            not isinstance(recorded, str)
            or _SHA256.fullmatch(recorded) is None
            or recorded != manifest["parent_digest"]
        ):
            raise ValueError("Recorded parent submission digest conflicts")
        matched = True
    artifact = parent / PAYLOAD_NAME
    if artifact.exists() or artifact.is_symlink():
        payload = _read_json_regular(artifact, "Preserved parent submission payload")
        if payload != report:
            raise ValueError("Preserved parent submission payload differs")
        matched = True
    if not matched:
        raise ValueError("Extension parent lacks preserved submission evidence")


def corroborate_parent(
    folder: Path,
    record: dict[str, Any],
    manifest: dict[str, str],
    data: dict[str, Any],
    addresses: Path,
    reporter: Any,
) -> None:
    """Fail-closed parent corroboration before reservation and on every retry.

    Reads the parent's private record from the fixed sibling layout with the
    nonblocking regular-file reader (#284); a missing, unreadable, non-object
    or non-regular input is refused without hanging. Any divergence between
    the record, the preserved artifacts, the recomputed payload, the ledger
    entry and the manifest fails closed before any ledger or municipal effect.
    """
    manifest = validate_extension(manifest)
    parent = _parent_folder(folder, manifest)
    parent_record = _read_json_regular(
        parent / "report.json", "Extension parent record"
    )
    _check_parent_record(parent_record, manifest)
    _check_photos(folder, record, parent, parent_record, manifest)
    check_parent_ledger(manifest, data)
    _corroborate_digest(parent, parent_record, manifest, addresses, reporter)
