"""Synthetic GET-only case-listing transport and privacy regressions."""

import io
import json
import traceback
import unittest
import urllib.error
from unittest.mock import patch

from parking_report.api106 import APIError, InputError, Municipality, NoRedirect

CREDS = {
    "subscription_key": "synthetic-key",
    "login": {"UserName": "test", "Password": "test"},
}
REPORTER = {"ContactIdType": "100000000", "ContactIdNumber": "000000018"}
TOKEN = "synthetic/token?secret=1"


class Opener:
    def __init__(self, payload=None, error=None):
        self.payload = payload if payload is not None else {"incidents": []}
        self.error = error
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error:
            raise self.error
        return io.BytesIO(json.dumps(self.payload).encode())


class CaseListingTests(unittest.TestCase):
    def call(self, opener, **kwargs):
        with patch(
            "parking_report.api106.urllib.request.build_opener", return_value=opener
        ) as factory:
            result = Municipality(CREDS).list_cases(
                TOKEN, REPORTER, from_date=kwargs.get("from_date", "2026-01-01")
            )
        self.assertIsInstance(factory.call_args.args[0], NoRedirect)
        return result

    def test_exact_get_shape_and_no_contact_write(self):
        opener = Opener(
            {
                "incidents": [
                    {
                        "case_number": "2601-1234567",
                        "description": "synthetic",
                        "statusname": "open",
                        "hasFiles": True,
                    }
                ]
            }
        )
        with (
            patch.object(Municipality, "contact", side_effect=AssertionError("write")),
            patch.object(Municipality, "create", side_effect=AssertionError("write")),
        ):
            result = self.call(opener)
        self.assertEqual(result["incidents"][0]["case_number"], "2601-1234567")
        self.assertEqual(len(opener.requests), 1)
        request, timeout = opener.requests[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertTrue(
            request.full_url.endswith(
                "/GetContactCases/synthetic%2Ftoken%3Fsecret%3D1/100000000/000000018?from=2026-01-01&caseType=2"
            )
        )
        self.assertEqual(timeout, 45)
        self.assertEqual(request.headers["Ocp-apim-subscription-key"], "synthetic-key")
        self.assertEqual(request.headers["User-agent"], "App 106")

    def test_empty_list_and_future_unknown_fields_preserved(self):
        self.assertEqual(self.call(Opener()), {"incidents": []})
        response = {"incidents": [{"future": {"nested": 1}}], "future_top": True}
        self.assertEqual(self.call(Opener(response)), response)

    def test_invalid_inputs_never_open_network(self):
        for token, reporter, start in [
            ("", REPORTER, "2026-01-01"),
            ("bad\n", REPORTER, "2026-01-01"),
            (TOKEN, {}, "2026-01-01"),
            (TOKEN, dict(REPORTER, ContactIdType="other"), "2026-01-01"),
            (TOKEN, dict(REPORTER, ContactIdNumber="000000000"), "2026-01-01"),
            (TOKEN, dict(REPORTER, ContactIdNumber="../secret"), "2026-01-01"),
            (TOKEN, dict(REPORTER, ContactIdNumber="000000019"), "2026-01-01"),
            (TOKEN, REPORTER, "2026-02-30"),
            (TOKEN, REPORTER, "2026-1-01"),
            (TOKEN, REPORTER, "2026-01-01&caseType=9"),
            (TOKEN, REPORTER, None),
        ]:
            with (
                self.subTest(start=start),
                patch("parking_report.api106.urllib.request.build_opener") as network,
                self.assertRaises(InputError),
            ):
                Municipality(CREDS).list_cases(token, reporter, from_date=start)
            network.assert_not_called()

    def test_failures_are_single_attempt_and_redacted(self):
        sensitive = TOKEN + REPORTER["ContactIdNumber"] + CREDS["subscription_key"]
        for error in [
            urllib.error.HTTPError(sensitive, 403, sensitive, {}, None),
            urllib.error.HTTPError(sensitive, 302, sensitive, {}, None),
            urllib.error.URLError(sensitive),
            OSError(sensitive),
        ]:
            opener = Opener(error=error)
            with self.subTest(error=type(error).__name__):
                try:
                    self.call(opener)
                except APIError:
                    rendered = traceback.format_exc()
                    for value in (
                        TOKEN,
                        REPORTER["ContactIdNumber"],
                        CREDS["subscription_key"],
                    ):
                        self.assertNotIn(value, rendered)
                else:
                    self.fail("Expected APIError")
                self.assertEqual(len(opener.requests), 1)

    def test_invalid_response_envelopes(self):
        for payload in [
            [],
            {"incidents": None},
            {"incidents": ["bad"]},
            {"Success": False, "incidents": []},
            {"unexpected": []},
        ]:
            with self.subTest(payload=payload), self.assertRaises(APIError):
                self.call(Opener(payload))

    def test_malformed_json_and_response_bound(self):
        for body in [b"not JSON", b"x" * (16 * 1024 * 1024 + 1)]:
            opener = Opener()
            with (
                patch.object(opener, "open", return_value=io.BytesIO(body)),
                self.assertRaises(APIError),
            ):
                self.call(opener)


if __name__ == "__main__":
    unittest.main()
