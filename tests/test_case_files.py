"""Synthetic owned-case membership and bounded attachment GET regressions."""

import base64
import io
import json
import traceback
import unittest
import urllib.error
from unittest.mock import patch

from test_case_listing import CREDS, REPORTER, TOKEN

from parking_report.api106 import APIError, InputError, Municipality, NoRedirect

CASE = "11111111-1111-4111-8111-111111111111"


class FilesTests(unittest.TestCase):
    def call(self, payload=None, owned=None, error=None):
        if payload is None:
            payload = [
                {
                    "Content": base64.b64encode(b"synthetic image").decode(),
                    "Name": "../../unsafe",
                }
            ]
        if owned is None:
            owned = [{"caseId": CASE, "hasFiles": True}]
        api = Municipality(CREDS)
        with (
            patch.object(
                api, "list_cases", return_value={"incidents": owned}
            ) as listing,
            patch("parking_report.api106.urllib.request.build_opener") as factory,
        ):
            opener = factory.return_value
            if error:
                opener.open.side_effect = error
            else:
                opener.open.return_value = io.BytesIO(json.dumps(payload).encode())
            result = api.case_files(
                TOKEN, REPORTER, case_id=CASE, from_date="2026-01-01"
            )
        listing.assert_called_once_with(TOKEN, REPORTER, from_date="2026-01-01")
        self.assertIsInstance(factory.call_args.args[0], NoRedirect)
        opener.open.assert_called_once()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertTrue(
            request.full_url.endswith(
                "/GetCaseFiles/synthetic%2Ftoken%3Fsecret%3D1/" + CASE
            )
        )
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 45)
        return result

    def test_one_get_per_case_returns_bytes_ignores_names_and_urls(self):
        self.assertEqual(self.call(), [b"synthetic image"])
        self.assertEqual(
            self.call(
                [
                    {"Content": "YQ==", "url": "https://invalid.example/secret"},
                    {"Content": "Yg=="},
                ]
            ),
            [b"a", b"b"],
        )
        self.assertEqual(self.call([]), [])

    def test_unowned_and_duplicate_case_never_get_files(self):
        for records in [
            [],
            [{"caseId": "22222222-2222-4222-8222-222222222222"}],
            [{"caseId": CASE}, {"caseId": CASE}],
            [{"caseId": None}],
        ]:
            api = Municipality(CREDS)
            with (
                self.subTest(records=records),
                patch.object(api, "list_cases", return_value={"incidents": records}),
                patch("parking_report.api106.urllib.request.build_opener") as network,
                self.assertRaises(InputError),
            ):
                api.case_files(TOKEN, REPORTER, case_id=CASE, from_date="2026-01-01")
            network.assert_not_called()

    def test_wrong_case_identifier_rejected_before_listing(self):
        for case_id in ["", "../escape", "web-case-id", None, "http://invalid"]:
            api = Municipality(CREDS)
            with (
                patch.object(api, "list_cases") as listing,
                self.assertRaises(InputError),
            ):
                api.case_files(TOKEN, REPORTER, case_id=case_id, from_date="2026-01-01")
            listing.assert_not_called()

    def test_invalid_attachment_shapes_fail_closed(self):
        for payload in [
            {},
            [None],
            [{"Content": None}],
            [{"Content": "%%%"}],
            [{"Content": ""}],
            [{"Content": "YQ=="}] * 21,
        ]:
            with (
                self.subTest(payload_type=type(payload).__name__),
                self.assertRaises(APIError),
            ):
                self.call(payload)

    def test_error_traceback_never_contains_secret_url(self):
        secret = TOKEN + REPORTER["ContactIdNumber"] + CREDS["subscription_key"]
        for error in [
            urllib.error.HTTPError(secret, 302, secret, {}, None),
            urllib.error.URLError(secret),
        ]:
            try:
                self.call(error=error)
            except APIError:
                rendered = traceback.format_exc()
                for value in (
                    TOKEN,
                    REPORTER["ContactIdNumber"],
                    CREDS["subscription_key"],
                ):
                    self.assertNotIn(value, rendered)
            else:
                self.fail("Expected redacted error")

    def test_membership_failure_does_not_fetch_attachments(self):
        api = Municipality(CREDS)
        with (
            patch.object(api, "list_cases", side_effect=APIError("Listing failed")),
            patch("parking_report.api106.urllib.request.build_opener") as network,
            self.assertRaises(APIError),
        ):
            api.case_files(TOKEN, REPORTER, case_id=CASE, from_date="2026-01-01")
        network.assert_not_called()

    def test_aggregate_and_per_file_bounds(self):
        api = Municipality(CREDS)
        with (
            patch.object(
                api, "list_cases", return_value={"incidents": [{"caseId": CASE}]}
            ),
            patch("parking_report.api106.urllib.request.build_opener") as factory,
        ):
            for raw in [b"x" * (32 * 1024 * 1024 + 1), b"not json"]:
                factory.return_value.open.return_value = io.BytesIO(raw)
                with self.assertRaises(APIError):
                    api.case_files(
                        TOKEN, REPORTER, case_id=CASE, from_date="2026-01-01"
                    )
        # Mock the decoder to test decoded-size rejection without huge fixtures.
        with (
            patch(
                "parking_report.api106.base64.b64decode",
                return_value=b"x" * (20 * 1024 * 1024 + 1),
            ),
            self.assertRaises(APIError),
        ):
            self.call()


if __name__ == "__main__":
    unittest.main()
