"""Offline transport-layer tests for the Municipality client: recorded responses and injected failures."""
from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from parking_report.api106 import APIError, InputError, Municipality

CREDS = {"subscription_key": "key-1",
         "login": {"UserName": "user", "Password": "pass"}}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self, outcome):
        self.outcome = outcome
        self.requests = []

    def open(self, request, timeout=45):
        self.requests.append(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return FakeResponse(self.outcome)


def patched(opener):
    return patch("parking_report.api106.urllib.request.build_opener", lambda *a, **k: opener)


class TransportTests(unittest.TestCase):
    def test_init_requires_all_credentials(self):
        for creds in ({}, {"subscription_key": "k"},
                      {"subscription_key": "k", "login": {"UserName": "", "Password": "p"}},
                      {"subscription_key": "k", "login": {"UserName": "u"}},
                      {"subscription_key": "", "login": {"UserName": "u", "Password": "p"}}):
            with self.subTest(creds=creds), self.assertRaises(InputError):
                Municipality(creds)

    def test_request_success_shape(self):
        opener = FakeOpener({"Success": True, "Token": "t-1"})
        with patched(opener):
            result = Municipality(CREDS).request("Login", {"UserName": "u"})
        self.assertEqual(result["Token"], "t-1")
        request = opener.requests[0]
        self.assertTrue(request.full_url.endswith("/CRMCityConnect/api/jerusalem/Login"))
        self.assertEqual(request.headers["Ocp-apim-subscription-key"], "key-1")
        self.assertEqual(request.headers["User-agent"], "App 106")
        self.assertEqual(json.loads(request.data.decode()), {"UserName": "u"})

    def test_request_rejects_non_success(self):
        with patched(FakeOpener({"Success": False})), \
                self.assertRaisesRegex(APIError, "did not confirm success"):
            Municipality(CREDS).request("Login", {})

    def test_request_http_error(self):
        error = urllib.error.HTTPError("http://x", 500, "err", {}, None)
        with patched(FakeOpener(error)), self.assertRaisesRegex(APIError, "Login: HTTP 500"):
            Municipality(CREDS).request("Login", {})

    def test_request_network_error(self):
        error = urllib.error.URLError("down")
        with patched(FakeOpener(error)), self.assertRaisesRegex(APIError, "network failure"):
            Municipality(CREDS).request("Login", {})

    def test_login_requires_token(self):
        with patched(FakeOpener({"Success": True})), self.assertRaisesRegex(APIError, "no token"):
            Municipality(CREDS).login()
        with patched(FakeOpener({"Success": True, "Token": "t-9"})):
            self.assertEqual(Municipality(CREDS).login(), "t-9")

    def test_contact_registration(self):
        reporter = {"FirstName": "דנה", "ContactIdInCRM": ""}
        with patched(FakeOpener({"Success": True, "ContactIdInCRM": "guid-1"})):
            contact = Municipality(CREDS).contact("t", reporter)
        self.assertEqual(contact["ContactIdInCRM"], "guid-1")
        self.assertEqual(contact["Token"], "t")
        with patched(FakeOpener({"Success": True})), \
                self.assertRaisesRegex(APIError, "no ID"):
            Municipality(CREDS).contact("t", dict(reporter, ContactIdInCRM=""))

    def test_create_and_attach_shapes(self):
        opener = FakeOpener({"Success": True, "CaseIdInCRM": "c-1", "WebCaseIdInCRM": "w-1"})
        api = Municipality(CREDS)
        with patched(opener):
            api.create("tok", {"contact": {"FirstName": "x"}}, {"CaseDescription": "d"})
        body = json.loads(opener.requests[-1].data.decode())
        self.assertEqual(body["Token"], "tok")
        self.assertEqual(body["CaseDescription"], "d")
        with patched(opener):
            api.attach("tok", {"CaseIdInCRM": "c-1", "WebCaseIdInCRM": "w-1"}, b"\xff\xd8img", "p.jpg")
        body = json.loads(opener.requests[-1].data.decode())
        self.assertEqual(body["caseIdInCRM"], "c-1")
        self.assertEqual(body["fileName"], "p.jpg")
        import base64
        self.assertEqual(base64.b64decode(body["fileContent"]), b"\xff\xd8img")


if __name__ == "__main__":
    unittest.main()
