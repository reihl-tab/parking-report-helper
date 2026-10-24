"""HTTP contract and real loopback tests; never contact the municipality."""

import io
import json
import threading
import unittest
import urllib.error
import urllib.request
from wsgiref.simple_server import make_server

from parking_report.api106 import APIError
from parking_report.http_service import QuietHandler, Service

TOKEN = "synthetic-only-token-0000000000000000"
ENV = {
    "PARKING_HTTP_TOKEN": TOKEN,
    "PARKING_106_SUBSCRIPTION_KEY": "fake-key",
    "PARKING_106_USERNAME": "fake-user",
    "PARKING_106_PASSWORD": "fake-password",
}


class Fake:
    calls = 0
    error = None

    def __init__(self, credentials):
        pass

    def login(self):
        Fake.calls += 1
        if Fake.error:
            raise Fake.error
        return "TOKEN_MUST_NEVER_ESCAPE"


class ServiceTests(unittest.TestCase):
    def setUp(self):
        Fake.calls = 0
        Fake.error = None
        self.app = Service(ENV, Fake)

    def call(self, path="/v1/login-check", method="POST", auth=True, **extra):
        env = {"PATH_INFO": path, "REQUEST_METHOD": method, "wsgi.input": io.BytesIO()}
        if auth:
            env["HTTP_AUTHORIZATION"] = "Bearer " + TOKEN
        env.update(extra)
        status = []
        body = b"".join(self.app(env, lambda code, headers: status.append(code)))
        self.assertNotIn(b"TOKEN_MUST_NEVER_ESCAPE", body)
        self.assertNotIn(b"fake-password", body)
        return status[0], json.loads(body)

    def test_startup_requires_private_token(self):
        with self.assertRaises(ValueError):
            Service({})

    def test_health_never_logs_in(self):
        self.assertEqual(self.call("/healthz", "GET", False)[0], "200 OK")
        self.assertEqual(Fake.calls, 0)

    def test_requires_auth(self):
        self.assertEqual(self.call(auth=False)[0], "401 Unauthorized")
        self.assertEqual(Fake.calls, 0)

    def test_report_hard_disabled(self):
        self.assertEqual(self.call("/v1/reports")[1]["status"], "submission_disabled")
        self.assertEqual(Fake.calls, 0)

    def test_one_login_no_token_return(self):
        self.assertEqual(self.call()[1]["status"], "login_ok")
        self.assertEqual(self.call()[0], "409 Conflict")
        self.assertEqual(Fake.calls, 1)

    def test_failed_login_consumed(self):
        Fake.error = APIError("Login: HTTP 403.")
        self.assertEqual(self.call()[1]["diagnostic"], "http_403")
        self.assertEqual(self.call()[0], "409 Conflict")
        self.assertEqual(Fake.calls, 1)

    def test_untrusted_error_not_forwarded(self):
        Fake.error = APIError("secret-server-value")
        self.assertEqual(self.call()[1]["diagnostic"], "login_failed")

    def test_exception_not_forwarded(self):
        Fake.error = ValueError("private-value")
        self.assertEqual(self.call()[1]["diagnostic"], "unknown")

    def test_missing_config(self):
        self.app = Service({"PARKING_HTTP_TOKEN": TOKEN}, Fake)
        self.assertEqual(self.call()[0], "503 Service Unavailable")
        self.assertEqual(Fake.calls, 0)

    def test_no_bodies_or_queries(self):
        for extra in [
            {"CONTENT_LENGTH": "100"},
            {"QUERY_STRING": "token=x"},
            {"HTTP_TRANSFER_ENCODING": "chunked"},
        ]:
            self.assertEqual(self.call(**extra)[0], "400 Bad Request")
        self.assertEqual(Fake.calls, 0)

    def test_method_and_route(self):
        self.assertEqual(self.call(method="GET")[0], "405 Method Not Allowed")
        self.assertEqual(self.call("/unknown")[0], "404 Not Found")
        self.assertEqual(Fake.calls, 0)

    def test_concurrent_one_call(self):
        threads = [threading.Thread(target=self.call) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(Fake.calls, 1)

    def test_real_http_loopback(self):
        with make_server(
            "127.0.0.1", 0, self.app, handler_class=QuietHandler
        ) as server:
            t = threading.Thread(target=server.serve_forever)
            t.start()
            try:
                base = "http://127.0.0.1:" + str(server.server_port)
                with urllib.request.urlopen(base + "/healthz") as r:
                    self.assertFalse(json.load(r)["submission_enabled"])
                req = urllib.request.Request(
                    base + "/v1/login-check",
                    data=b"",
                    headers={"Authorization": "Bearer " + TOKEN},
                    method="POST",
                )
                with urllib.request.urlopen(req) as r:
                    self.assertEqual(json.load(r)["status"], "login_ok")
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(req)
                self.assertEqual(caught.exception.code, 409)
                self.assertEqual(Fake.calls, 1)
            finally:
                server.shutdown()
                t.join()
