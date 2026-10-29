"""Post-submit duplicate detection (#160).

The municipality has created two CRM cases from a single CreateCase POST,
seconds apart. After a successful submit the CLI waits briefly, re-lists the
reporter's cases for today and counts cases carrying the exact submitted
text. More than one match means a server-side duplicate: the run exits with
the dedicated status and prints the case numbers only. A failed verification
never fails a finished submission.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from parking_report import api106, api_cli

ENV = {
    "PARKING_REPORTER_FIRST_NAME": "דנה",
    "PARKING_REPORTER_LAST_NAME": "לוי",
    "PARKING_REPORTER_PHONE": "0500000000",
    "PARKING_REPORTER_ID_NUMBER": "000000018",
    "PARKING_REPORTER_EMAIL": "dana@example.com",
    "PARKING_106_ENABLE_LIVE": "true",
}

DESCRIPTION = "גרפיטי על קיר ברחוב לדוגמה 1. אבקש לטפל במפגע."


class Out(io.StringIO):
    def reconfigure(self, encoding=None):
        pass


def make_folder(tmp):
    folder = Path(tmp)
    Image.new("RGB", (10, 10), "white").save(folder / "photo.jpg")
    (folder / "report.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "ready",
                "photo": "photo.jpg",
                "captured_at": "2026-01-01 12:00",
                "address": {"street": "רחוב לדוגמה", "house_number": "1"},
                "violation": {"category": "graffiti", "description": "גרפיטי על קיר"},
                "municipal_subject": {
                    "code": "1072",
                    "owner_choice": True,
                    "event_type": "graffiti",
                },
                "approved_description": DESCRIPTION,
            }
        ),
        encoding="utf-8",
    )
    addresses = folder / "addresses.json"
    addresses.write_text(
        json.dumps(
            {
                "version": 1,
                "streets": [{"StreetCode": "fake", "StreetName": "רחוב לדוגמה"}],
                "addresses": [[31.77, 35.21, "1", "רחוב לדוגמה"]],
            }
        ),
        encoding="utf-8",
    )
    return folder, addresses


class FakeLedger:
    def __init__(self, _path):
        pass

    @contextmanager
    def locked(self):
        yield self

    def reserve(self, report_id, digest, photo_hash):
        return {"state": {"version": 1, "status": "prepared"}}

    def checkpoint(self, report_id, state):
        pass


class FakeMunicipalityCtor:
    def __init__(self, *_args, **_kwargs):
        pass


def fake_submit(**overrides):
    result = {
        "status": "submitted",
        "case": {"WebCaseIdInCRM": "w", "CaseIdInCRM": "c"},
        "photo_attached": True,
        "state_file": "state.json",
    }
    result.update(overrides)

    def run(*_args, **_kwargs):
        return result

    return run


def run_submit(folder, addresses, check_result):
    out = Out()
    with (
        patch.object(sys, "stdout", out),
        patch.dict("os.environ", ENV, clear=False),
        patch("parking_report.ledger.GitLedger", FakeLedger),
        patch("parking_report.api106.submit", fake_submit()),
        patch("parking_report.api106.Municipality", FakeMunicipalityCtor),
        patch(
            "parking_report.api_cli.post_submit_duplicate_check",
            lambda *_args, **_kwargs: check_result,
        ) as check,
    ):
        dry = Out()
        with (
            patch.object(sys, "stdout", dry),
            patch.dict("os.environ", ENV, clear=False),
        ):
            api_cli.main([str(folder), "--addresses", str(addresses)])
        digest = json.loads(dry.getvalue())["sha256"]
        code = api_cli.main(
            [
                str(folder),
                "--addresses",
                str(addresses),
                "--submit",
                "--approved-sha256",
                digest,
                "--ledger-dir",
                str(folder),
            ]
        )
    return code, json.loads(out.getvalue()), check


class SubmitDuplicateExitTests(unittest.TestCase):
    def test_duplicate_found_exits_with_dedicated_status(self):
        with TemporaryDirectory() as tmp:
            folder, addresses = make_folder(tmp)
            code, payload, check = run_submit(
                folder, addresses, ["2610-9222943", "2610-9222944"]
            )
        self.assertEqual(code, api_cli.DUPLICATE_SUBMIT_EXIT_CODE)
        self.assertEqual(payload["status"], "submitted")
        self.assertEqual(payload["duplicate_check"], "duplicates_found")
        self.assertEqual(
            payload["duplicate_case_numbers"], ["2610-9222943", "2610-9222944"]
        )
        self.assertNotIn(DESCRIPTION, json.dumps(payload, ensure_ascii=False))

    def test_single_case_stays_clean(self):
        with TemporaryDirectory() as tmp:
            folder, addresses = make_folder(tmp)
            code, payload, check = run_submit(folder, addresses, ["2610-9222943"])
        self.assertEqual(code, 0)
        self.assertEqual(payload["duplicate_check"], "single")
        self.assertEqual(payload["duplicate_case_numbers"], [])

    def test_zero_matches_reports_pending_not_single(self):
        with TemporaryDirectory() as tmp:
            folder, addresses = make_folder(tmp)
            code, payload, check = run_submit(folder, addresses, [])
        self.assertEqual(code, 0)
        self.assertEqual(payload["duplicate_check"], "pending")
        self.assertEqual(payload["duplicate_case_numbers"], [])

    def test_unavailable_verification_never_fails_the_submission(self):
        with TemporaryDirectory() as tmp:
            folder, addresses = make_folder(tmp)
            code, payload, check = run_submit(folder, addresses, None)
        self.assertEqual(code, 0)
        self.assertEqual(payload["duplicate_check"], "unavailable")

    def test_already_submitted_skips_the_check(self):
        out = Out()
        with TemporaryDirectory() as tmp:
            folder, addresses = make_folder(tmp)
            with (
                patch.object(sys, "stdout", out),
                patch.dict("os.environ", ENV, clear=False),
                patch("parking_report.ledger.GitLedger", FakeLedger),
                patch(
                    "parking_report.api106.submit",
                    fake_submit(status="already_submitted"),
                ),
                patch("parking_report.api106.Municipality", FakeMunicipalityCtor),
                patch("parking_report.api_cli.post_submit_duplicate_check") as check,
            ):
                dry = Out()
                with patch.object(sys, "stdout", dry):
                    api_cli.main([str(folder), "--addresses", str(addresses)])
                digest = json.loads(dry.getvalue())["sha256"]
                code = api_cli.main(
                    [
                        str(folder),
                        "--addresses",
                        str(addresses),
                        "--submit",
                        "--approved-sha256",
                        digest,
                        "--ledger-dir",
                        str(folder),
                    ]
                )
        self.assertEqual(code, 0)
        line = json.loads(out.getvalue())
        self.assertEqual(line["duplicate_check"], "skipped")
        self.assertEqual(line["duplicate_case_numbers"], [])
        check.assert_not_called()


class FakeMunicipality:
    def __init__(self, incidents=None, error=None):
        self.incidents = incidents if incidents is not None else []
        self.error = error

    def login(self):
        return "token"

    def list_cases(self, token, reporter, *, from_date):
        if self.error is not None:
            raise self.error
        return {"incidents": self.incidents}


class ExitCodePinTests(unittest.TestCase):
    def test_duplicate_exit_code_is_the_documented_five(self):
        # The constant is part of the CLI contract: pin the value, not the name.
        self.assertEqual(api_cli.DUPLICATE_SUBMIT_EXIT_CODE, 5)


class PostSubmitDuplicateCheckTests(unittest.TestCase):
    def test_waits_then_matches_exact_descriptions(self):
        incidents = [
            {"case_number": "2610-9222953", "description": DESCRIPTION},
            {"case_number": "2610-9222952", "description": DESCRIPTION},
            {"case_number": "2610-9222901", "description": "אחר"},
            {"case_number": "2610-9222902", "description": DESCRIPTION + " "},
            "not-a-dict",
            {"description": DESCRIPTION},
        ]
        delays = []
        numbers = api_cli.post_submit_duplicate_check(
            FakeMunicipality(incidents),
            {"ContactIdNumber": "000000018", "ContactIdType": "100000000"},
            DESCRIPTION,
            sleep=delays.append,
            today="2026-10-29",
        )
        self.assertEqual(delays, [api_cli.DUPLICATE_CHECK_DELAY_SECONDS])
        self.assertEqual(numbers, ["2610-9222952", "2610-9222953"])

    def test_listing_uses_the_reporters_jerusalem_date(self):
        captured = {}

        class FakeDatetime:
            @staticmethod
            def now(tz=None):
                captured["tz"] = tz
                from datetime import datetime as real_datetime

                return real_datetime(2026, 10, 29, 0, 30)

        api = FakeMunicipality([])
        original = api.list_cases

        def spy(token, reporter, *, from_date):
            captured["from_date"] = from_date
            return original(token, reporter, from_date=from_date)

        api.list_cases = spy
        with patch("parking_report.api_cli.datetime", FakeDatetime):
            api_cli.post_submit_duplicate_check(
                api,
                {"ContactIdNumber": "000000018", "ContactIdType": "100000000"},
                DESCRIPTION,
                sleep=lambda _seconds: None,
                today=None,
            )
        from zoneinfo import ZoneInfo

        self.assertEqual(captured["tz"], ZoneInfo("Asia/Jerusalem"))
        self.assertEqual(captured["from_date"], "2026-10-29")

    def test_verification_failure_returns_none(self):
        numbers = api_cli.post_submit_duplicate_check(
            FakeMunicipality(error=ConnectionError("down")),
            {"ContactIdNumber": "000000018", "ContactIdType": "100000000"},
            DESCRIPTION,
            sleep=lambda _seconds: None,
            today="2026-10-29",
        )
        self.assertIsNone(numbers)


class DuplicateCaseNumbersTests(unittest.TestCase):
    def test_rejects_empty_description(self):
        with self.assertRaises(api106.InputError):
            api106.duplicate_case_numbers([], "")

    def test_ignores_non_list_incidents(self):
        self.assertEqual(api106.duplicate_case_numbers(None, DESCRIPTION), [])


if __name__ == "__main__":
    unittest.main()
