"""Offline preview by default; one exact reviewed input may be submitted explicitly."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
        check_text(address["street"], "Street"), str(address["house_number"]), gps
    )
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
    canonical = json.dumps(fingerprint, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return contact, report, jpeg, digest


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
    p.add_argument(
        "--approved-category-routing",
        choices=("crosswalk_parking", "blocked_ramp"),
        default="",
        help="Deprecated: historical exact routing to 1145; use report.json municipal_subject",
    )
    args = p.parse_args(argv)
    try:
        contact, report, jpeg, digest = prepare(
            args.folder, args.addresses, private_values(os.environ)
        )
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
        with ledger.locked() if ledger else nullcontext():
            entry = ledger.reserve(report_id, digest, photo_hash) if ledger else None
            return execute(
                args, contact, report, jpeg, digest, ledger, entry, report_id
            )
    except Exception:  # noqa: BLE001 - fail-closed boundary; no exception text exposed
        print(
            '{"status":"blocked","note":"Check private input and ledger; no automatic retry."}'
        )
        return 4


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
