"""Validate an explicit shared-photo issue pair; never infer permission.

One unchanged original photo may ground two distinct non-vehicle municipal
issues (for example a damaged sign and a missing bollard at one spot). Each
issue keeps its own intake, digest, receipt and state. The manifest is an
audit record bound into each member's digest; a live member also needs
``--approved-shared-issue-sha256``. Vehicle pairs keep using ``shared_photo``
(#271): a record with a plate is refused here.

The durable ledger stays free of report content: ``group_fingerprint`` covers
intake IDs, event types, the photo hash and the authority reference only.

Late join: the first issue may already be filed as an ordinary intake before
the second is requested. The pair is then allowed only when the photo's
origin intake is named by the manifest, is complete, and carries no other
relation. The origin's ledger entry, digest and state never change.

Because the ledger holds no report content, a member's reservation also reads
its sibling's record, assumed to sit beside it as ``<parent>/<sibling ID>``,
and refuses unless both manifests are byte-identical (fail-closed), exactly
like the vehicle pair sibling check (#271, #284).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .shared_photo import _is_intake_id, _read_regular

_FIELDS = {"intake_ids", "issues", "photo_sha256", "authority_reference"}
_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_:/.-]{0,199}")
_EVENT = re.compile(r"[a-z0-9][a-z0-9_]{0,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")

GROUP_SIZE = 2  # Exactly two distinct issues per original photo.


def _pair(value: Any, check: Any, what: str) -> list[str]:
    """Exactly GROUP_SIZE distinct strings that pass check."""
    if (
        not isinstance(value, list)
        or len(value) != GROUP_SIZE
        or not all(isinstance(item, str) and check(item) for item in value)
    ):
        raise ValueError(f"Invalid shared-issue {what}")
    if len(set(value)) != GROUP_SIZE:
        raise ValueError(f"Shared-issue {what} must be distinct")
    return list(value)


def _text(value: Any, pattern: re.Pattern[str], what: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"Invalid shared-issue {what}")
    return value


def validate_issue_pair(value: Any) -> dict[str, Any]:
    """Return the exact validated manifest; reject partial or unknown fields."""
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise ValueError("Shared-issue manifest must contain the exact required fields")
    intake_ids = _pair(value["intake_ids"], _is_intake_id, "intake IDs")
    if intake_ids != sorted(intake_ids):
        raise ValueError("Shared-issue intake IDs must be sorted")
    return {
        "intake_ids": intake_ids,
        "issues": _pair(
            value["issues"], lambda item: _EVENT.fullmatch(item) is not None, "issues"
        ),
        "photo_sha256": _text(value["photo_sha256"], _SHA256, "photo hash"),
        "authority_reference": _text(
            value["authority_reference"], _REFERENCE, "authority reference"
        ),
    }


def member_manifest(
    record: dict[str, Any], intake_id: str, photo_hash: str
) -> dict[str, Any]:
    """Validate the record's manifest for this intake and its original photo."""
    for key in ("followup", "shared_photo", "shared_photo_extension", "supersedes"):
        if key in record:
            raise ValueError("A shared-issue member cannot combine another relation")
    manifest = validate_issue_pair(record["shared_photo_issue"])
    if intake_id not in manifest["intake_ids"]:
        raise ValueError("Intake is not named by its shared-issue manifest")
    if manifest["photo_sha256"] != photo_hash:
        raise ValueError("Shared-issue manifest names another original photo")
    if record.get("plate"):
        raise ValueError("A vehicle report pairs through shared_photo, not issues")
    slot = manifest["intake_ids"].index(intake_id)
    choice = record.get("municipal_subject")
    if (
        not isinstance(choice, dict)
        or choice.get("owner_choice") is not True
        or choice.get("event_type") != manifest["issues"][slot]
    ):
        raise ValueError("Intake event type does not match its shared-issue entry")
    return manifest


def group_fingerprint(manifest: dict[str, Any]) -> str:
    """Content-free group identity for the ledger: IDs, issues, photo, authority."""
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def canonical_manifest(manifest: dict[str, Any]) -> bytes:
    """Canonical bytes of a validated manifest."""
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()


def sibling_record_path(folder: Path, manifest: dict[str, Any]) -> Path:
    """<folder>.parent/<sibling intake ID>/report.json, ID an opaque folder name."""
    others = [i for i in manifest["intake_ids"] if i != folder.name]
    if len(others) != 1 or not _is_intake_id(others[0]):
        raise ValueError("Shared-issue sibling must be an opaque intake folder name")
    return folder.parent / others[0] / "report.json"


def _sibling_record(path: Path) -> dict[str, Any]:
    try:
        record = json.loads(_read_regular(path))
    except (OSError, ValueError):
        raise ValueError(
            "Shared-issue sibling record is missing or unreadable"
        ) from None
    if not isinstance(record, dict) or "shared_photo_issue" not in record:
        raise ValueError("Shared-issue sibling record has no manifest")
    return record


def check_sibling(folder: Path, manifest: Any) -> None:
    """Fail closed unless the sibling's record carries the identical manifest.

    The ledger is content-free, so the sibling record itself must also pass
    the member checks: plate-free, no combined relation, and a municipal
    subject matching its own manifest slot. Otherwise a completed vehicle
    intake could be paired as an "issue" origin by editing its report.json.
    """
    own = validate_issue_pair(manifest)
    try:
        record = _sibling_record(sibling_record_path(folder, own))
        theirs = validate_issue_pair(record["shared_photo_issue"])
    except ValueError:
        raise ValueError("Shared-issue sibling manifest differs") from None
    if canonical_manifest(theirs) != canonical_manifest(own):
        raise ValueError("Shared-issue sibling manifest differs")
    sibling_id = next(i for i in own["intake_ids"] if i != folder.name)
    try:
        member_manifest(record, sibling_id, own["photo_sha256"])
    except ValueError:
        raise ValueError(
            "Shared-issue sibling record fails the member checks"
        ) from None
