"""Offline preview by default; one exact reviewed input may be submitted explicitly."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .intake import record_to_car
from .report import build_report
from .subjects import select_subject, validate_owner_choice


def private_values(env: Mapping[str, str]) -> dict[str, str]:
    """Read reporter variables without printing sensitive values."""
    keys = ("first_name", "last_name", "phone", "id_number", "email")
    values = {k: env.get("PARKING_REPORTER_" + k.upper(), "") for k in keys}
    if not all(values.values()):
        raise ValueError(
            "Set all PARKING_REPORTER_* values in private secrets/environment."
        )
    return values


def prepare(
    folder: Path, addresses: Path, reporter: Mapping[str, str]
) -> tuple[dict[str, Any], dict[str, Any], bytes, str]:
    """Validate the original private record and prepare the same payload for both modes."""
    from .api106 import Addresses, check_text, make_contact, read_picture

    record = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    car = record_to_car(record)
    name = record.get("photo", "")
    if not isinstance(name, str) or not name or Path(name).name != name:
        raise ValueError(
            "report.json must name one local photo, without path traversal"
        )
    photo = folder / name
    if photo.is_symlink():
        raise ValueError("Photo symlinks are not allowed")
    photo_hash, gps, jpeg = read_picture(photo)
    contact = make_contact(
        argparse.Namespace(
            first_name=reporter["first_name"],
            last_name=reporter["last_name"],
            phone=reporter["phone"],
            national_id=reporter["id_number"],
            email=reporter["email"],
        )
    )
    address = record["address"]
    resolved = Addresses(addresses).resolve(
        check_text(address["street"], "Street"),
        str(address["house_number"]),
        gps,
        # Only a JSON true opts in; "false", 0 or 1 stay fail-closed (#317).
        approximate=address.get("approximate") is True,
    )
    if address.get("approximate") is True:
        if resolved["coordinate_source"] == "approximate_street_centroid":
            # No GPS and no municipal point: owner-confirmed number, centroid pin.
            car["notes"] += " (מיקום משוער: מרכז הרחוב; המספר אומת מול הבעלים)"
        elif gps:
            car["notes"] += " לפי הבניין הממוספר הקרוב לנקודת הצילום"
    description = build_report(car)
    if "approved_description" in record:
        check_text(record["approved_description"], "Reviewed report text")
        description = record[
            "approved_description"
        ]  # Preserve exact reviewed whitespace too.
    report = {
        "CaseDescription": description,
        "CaseSubjectCode": select_subject(record),
        "CaseStreetCode": resolved["street_code"],
        "CaseStreetName": resolved["street_name"],
        "CaseHouseNumber": resolved["house"],
        "CaseAddressText": resolved["street_name"] + " " + resolved["house"],
        "CoordinateX": resolved["x"],
        "CoordinateY": resolved["y"],
        "Language": 1,
        "ApplicationCaseNumber": "ApplicationCaseNumber_VAL",
    }
    fingerprint = {"reporter": contact, "report": report, "picture": photo_hash}
    if record.get("municipal_subject") is not None:
        # Preserve historical fingerprints/receipts when using the unchanged default.
        fingerprint.update(
            event_type=record.get("violation", {}).get("category"),
            municipal_subject=record["municipal_subject"],
        )
    _bind_relations(record, folder.name, photo_hash, fingerprint)
    return contact, report, jpeg, _digest(fingerprint)


def _bind_relations(
    record: dict[str, Any], intake_id: str, photo_hash: str, fingerprint: dict[str, Any]
) -> None:
    """Bind every declared relation manifest into the preview digest."""
    if "supersedes" in record:
        from .supersedes import validate_supersedes

        if any(
            key in record
            for key in (
                "followup",
                "shared_photo",
                "shared_photo_extension",
                "shared_photo_issue",
            )
        ):
            raise ValueError("A supersedes manifest cannot combine another relation")
        # The manifest and the new intake ID enter the preview digest, so the
        # corrected re-filing never shares its parent's digest.
        fingerprint["supersedes"] = dict(
            validate_supersedes(record["supersedes"], intake_id),
            member=intake_id,
        )
    if "followup" in record:
        from .followup import validate_followup

        fingerprint["followup"] = validate_followup(record["followup"])
    if "shared_photo" in record and "shared_photo_issue" in record:
        raise ValueError("A record cannot carry both shared-photo manifests")
    if "shared_photo" in record:
        from .shared_photo import member_manifest

        # The member's own ID keeps each car's digest, state and receipt distinct.
        manifest = member_manifest(record, intake_id, photo_hash)
        fingerprint["shared_photo"] = dict(manifest, member=intake_id)
    if "shared_photo_issue" in record:
        from .shared_issue import member_manifest

        # The member's own ID keeps each issue's digest, state and receipt distinct.
        issue_manifest = member_manifest(record, intake_id, photo_hash)
        fingerprint["shared_photo_issue"] = dict(issue_manifest, member=intake_id)
    if "shared_photo_extension" in record:
        from .shared_photo_extension import extension_manifest

        # Every manifest field plus the new intake ID enters the preview digest.
        extension = extension_manifest(record, intake_id, photo_hash)
        fingerprint["shared_photo_extension"] = dict(extension, member=intake_id)
    if "supersedes" in fingerprint:
        # Exact duplicate (#260): the corrected payload without its manifest
        # must differ from the parent's reviewed digest.
        base = {k: v for k, v in fingerprint.items() if k != "supersedes"}
        if _digest(base) == fingerprint["supersedes"]["parent_digest"]:
            raise ValueError("A corrected re-filing must change its parent's payload")


def _digest(fingerprint: Mapping[str, Any]) -> str:
    canonical = json.dumps(fingerprint, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("folder", type=Path)
    p.add_argument("--addresses", type=Path, required=True)
    p.add_argument("--state-dir", type=Path, default=Path(".106-state"))
    p.add_argument(
        "--ledger-dir", type=Path, help="Private checked-out 106-ledger branch"
    )
    p.add_argument("--submit", action="store_true")
    p.add_argument("--approved-sha256", default="")
    p.add_argument("--retry-photo", action="store_true")
    p.add_argument("--approved-followup-sha256", default="")
    p.add_argument("--approved-shared-photo-sha256", default="")
    p.add_argument("--approved-shared-issue-sha256", default="")
    p.add_argument("--approved-extension-sha256", default="")
    p.add_argument("--approved-supersedes-sha256", default="")
    p.add_argument(
        "--approved-category-routing",
        choices=("crosswalk_parking", "blocked_ramp"),
        default="",
        help="Deprecated: historical exact routing to 1145; use report.json municipal_subject",
    )
    args = p.parse_args(argv)
    try:
        reporter = private_values(os.environ)
        contact, report, jpeg, digest = prepare(args.folder, args.addresses, reporter)
        from .ledger import GitLedger

        ledger = GitLedger(args.ledger_dir) if args.ledger_dir else None
        if args.submit and ledger is None:
            raise ValueError("Live submission requires a durable private ledger")
        # Derive the stable ID from the preserved intake folder, not mutable payload content.
        report_id = args.folder.name
        from .api106 import read_picture

        record_for_photo = json.loads(
            (args.folder / "report.json").read_text(encoding="utf-8")
        )
        photo_hash, _, _ = read_picture(args.folder / record_for_photo["photo"])
        followup = record_for_photo.get("followup")
        shared = record_for_photo.get("shared_photo")
        issue = record_for_photo.get("shared_photo_issue")
        extension = record_for_photo.get("shared_photo_extension")
        supersedes = record_for_photo.get("supersedes")
        _approval_gate(
            args, followup, args.approved_followup_sha256, digest, "Follow-up"
        )
        _approval_gate(
            args, shared, args.approved_shared_photo_sha256, digest, "Shared photo"
        )
        _approval_gate(
            args, issue, args.approved_shared_issue_sha256, digest, "Shared issue"
        )
        _approval_gate(
            args, extension, args.approved_extension_sha256, digest, "Extension"
        )
        _approval_gate(
            args, supersedes, args.approved_supersedes_sha256, digest, "Supersedes"
        )
        # Follow-up, shared-photo, extension and supersedes previews reserve nothing.
        exception = any(
            value is not None
            for value in (followup, shared, issue, extension, supersedes)
        )
        reserve_ledger = ledger if args.submit or not exception else None
        with reserve_ledger.locked() if reserve_ledger else nullcontext():
            entry = None
            if reserve_ledger:
                entry = _reserve(
                    reserve_ledger,
                    args.folder,
                    record_for_photo,
                    digest,
                    photo_hash,
                    followup,
                    shared,
                    extension,
                    args.addresses,
                    reporter,
                    supersedes,
                    issue,
                )
            return execute(
                args, contact, report, jpeg, digest, ledger, entry, report_id
            )
    except Exception:  # noqa: BLE001 - fail-closed boundary; no exception text exposed
        print(
            '{"status":"blocked","note":"Check private input and ledger; no automatic retry."}'
        )
        return 4


def _approval_gate(
    args: Any, manifest: Any, approved: str, digest: str, name: str
) -> None:
    """A manifest is never permission: live use needs its own exact digest gate."""
    if (
        args.submit
        and manifest is not None
        and (
            approved != digest
            or args.approved_sha256 != digest
            or os.environ.get("PARKING_106_ENABLE_LIVE") != "true"
        )
    ):
        raise ValueError(f"{name} needs explicit digest approval before reservation")
    if approved and manifest is None:
        raise ValueError(f"{name} approval requires a manifest")


def _guard_key(env: Mapping[str, str]) -> Any:
    """The runtime-only private guard key from the private environment (#293).

    The seed is a GitHub Actions Secret in the private reports environment,
    supplied only at runtime as hex of at least 16 bytes; it is never a
    GitHub Variable, never logged and never provisioned here.
    """
    from .vehicle_guard import GuardKey

    seed_text = env.get("PARKING_GUARD_SEED", "")
    if re.fullmatch(r"(?:[0-9a-f]{2}){16,}", seed_text) is None:
        raise ValueError("Private guard seed is missing or invalid")
    return GuardKey(env.get("PARKING_GUARD_KEY_ID", ""), bytes.fromhex(seed_text))


def _reserve(
    ledger: Any,
    folder: Path,
    record: Any,
    digest: str,
    photo_hash: str,
    followup: Any,
    shared: Any,
    extension: Any = None,
    addresses: Any = None,
    reporter: Any = None,
    supersedes: Any = None,
    issue: Any = None,
) -> Any:
    report_id = folder.name
    if supersedes is not None:
        return ledger.reserve(report_id, digest, photo_hash, supersedes=supersedes)
    if followup is not None:
        return ledger.reserve(report_id, digest, photo_hash, followup=followup)
    if shared is not None:
        from .shared_photo import check_sibling

        # Fail closed: the plate-free ledger cannot compare the two manifests.
        check_sibling(folder, shared)
        return ledger.reserve(report_id, digest, photo_hash, shared_photo=shared)
    if issue is not None:
        from .shared_issue import check_sibling

        # Fail closed: the content-free ledger cannot compare the two manifests.
        check_sibling(folder, issue)
        return ledger.reserve(report_id, digest, photo_hash, shared_issue=issue)
    if extension is not None:
        from .shared_photo_extension import corroborate_parent

        # Fail closed: parent artifacts corroborate the ledger digest, and the
        # provisioned guard index is verified, before any reservation. Every
        # retry rechecks both private records.
        guard = _guard_key(os.environ)
        corroborate_parent(folder, record, extension, ledger.data, addresses, reporter)
        return ledger.reserve(
            report_id, digest, photo_hash, extension=extension, guard=guard
        )
    return ledger.reserve(report_id, digest, photo_hash)


DUPLICATE_CHECK_DELAY_SECONDS = 20
DUPLICATE_SUBMIT_EXIT_CODE = 5


def post_submit_duplicate_check(
    api: Any,
    reporter: Any,
    description: str,
    *,
    sleep: Any = time.sleep,
    today: Any = None,
) -> Any:
    """Detect a server-side duplicate of a case we just submitted (#160).

    The municipality has created a second CRM case from one CreateCase POST,
    seconds later. Wait briefly, then count today's cases carrying our exact
    submitted text. Returns the sorted matching case numbers, or None when the
    verification itself failed: that must never fail a finished submission.
    """
    from .api106 import duplicate_case_numbers

    sleep(DUPLICATE_CHECK_DELAY_SECONDS)
    if today is None:
        # The municipal day boundary is the reporter's, not the server's.
        today = datetime.now(ZoneInfo("Asia/Jerusalem")).date().isoformat()
    try:
        token = api.login()
        incidents = api.list_cases(token, reporter, from_date=today).get(
            "incidents", []
        )
    except Exception:  # noqa: BLE001 - verification is best-effort by design
        return None
    return duplicate_case_numbers(incidents, description)


def execute(
    args: Any,
    contact: dict[str, Any],
    report: dict[str, Any],
    jpeg: bytes,
    digest: str,
    ledger: Any,
    entry: Any,
    report_id: str,
) -> int:
    from .api106 import Municipality, submit

    if not args.submit:
        if args.retry_photo:
            raise ValueError("--retry-photo requires explicit submission approval")
        # Logs contain no reporter, plate, address, description, or photo metadata.
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "sha256": digest,
                    "photo_upload_bytes": len(jpeg),
                    "network_requests": 0,
                }
            )
        )
        return 0
    if (
        os.environ.get("PARKING_106_ENABLE_LIVE") != "true"
        or args.approved_sha256 != digest
    ):
        raise ValueError(
            "Live submission needs private enablement and the exact reviewed digest"
        )
    record = json.loads((args.folder / "report.json").read_text(encoding="utf-8"))
    category = record.get("violation", {}).get("category")
    routing = getattr(args, "approved_category_routing", "")
    if routing:
        # Compatibility only for already reviewed historic reports. Never a global code switch.
        if (
            record.get("municipal_subject") is not None
            or report.get("CaseSubjectCode") != "1145"
            or category not in {"crosswalk_parking", "blocked_ramp"}
            or routing != category
        ):
            raise ValueError(
                "Legacy routing applies only to the exact historic 1145 event"
            )
    else:
        validate_owner_choice(record, report["CaseSubjectCode"])
    credentials = {
        "subscription_key": os.environ.get("PARKING_106_SUBSCRIPTION_KEY", ""),
        "login": {
            "UserName": os.environ.get("PARKING_106_USERNAME", ""),
            "Password": os.environ.get("PARKING_106_PASSWORD", ""),
        },
    }
    result = submit(
        Municipality(credentials),
        contact,
        report,
        jpeg,
        digest,
        args.state_dir,
        args.retry_photo,
        checkpoint=lambda state: ledger.checkpoint(report_id, state),
        initial_state=entry["state"],
    )
    # Case receipts remain in the private state ledger, not action logs.
    output = {
        "status": result["status"],
        "sha256": digest,
        "photo_attached": result["photo_attached"],
    }
    exit_code = 0
    output["duplicate_check"] = "skipped"
    output["duplicate_case_numbers"] = []
    description = report.get("CaseDescription")
    if result["status"] == "submitted" and isinstance(description, str) and description:
        numbers = post_submit_duplicate_check(
            Municipality(credentials), contact, description
        )
        if numbers is None:
            output["duplicate_check"] = "unavailable"
        elif len(numbers) > 1:
            # Numbers only: descriptions, plates and addresses stay out of logs.
            output["duplicate_check"] = "duplicates_found"
            output["duplicate_case_numbers"] = numbers
            exit_code = DUPLICATE_SUBMIT_EXIT_CODE
        elif numbers:
            output["duplicate_check"] = "single"
        else:
            # The CRM listing can lag creation by minutes; the next scheduled
            # sync remains the net that catches a late duplicate.
            output["duplicate_check"] = "pending"
    print(json.dumps(output, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
