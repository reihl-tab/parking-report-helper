"""End-to-end submission flow: recorded API responses through every submit() state transition."""
from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from parking_report.api106 import ReviewNeeded, submit


class FakeAPI:
    """Recorded municipal API: login -> contact -> create -> attach, with injectable failures."""

    def __init__(self, fail_create=False, fail_attach_once=False):
        self.calls = []
        self.fail_create = fail_create
        self.fail_attach_once = fail_attach_once

    def login(self):
        self.calls.append("login")
        return "tok-1"

    def contact(self, token, reporter):
        self.calls.append(("contact", token))
        return dict(reporter, ContactIdInCRM="guid-1")

    def create(self, token, contact, report):
        self.calls.append(("create", report.get("CaseSubjectCode")))
        if self.fail_create:
            raise ConnectionError("network dropped after create")
        return {"CaseIdInCRM": "c-1", "WebCaseIdInCRM": "w-1", "CaseNumberInCRM": "2610-1"}

    def attach(self, token, case, jpeg, filename):
        self.calls.append(("attach", filename, base64.b64encode(jpeg).decode()))
        if self.fail_attach_once:
            self.fail_attach_once = False
            raise TimeoutError("attach not confirmed")
        return {"Success": True}


REPORTER = {"FirstName": "דנה", "ContactIdInCRM": ""}
REPORT = {"CaseDescription": "d", "CaseSubjectCode": "1072"}
JPEG = b"\xff\xd8fake-jpeg"


class SubmitFlowTests(unittest.TestCase):
    def run_submit(self, tmp, api, fingerprint="fp-1", retry_photo=False):
        return submit(api, REPORTER, REPORT, JPEG, fingerprint, Path(tmp), retry_photo)

    def test_full_flow_records_every_step(self):
        with TemporaryDirectory() as tmp:
            api = FakeAPI()
            result = self.run_submit(tmp, api)
            self.assertEqual(result["status"], "submitted")
            self.assertTrue(result["photo_attached"])
            self.assertEqual(api.calls[0], "login")
            self.assertEqual(api.calls[1], ("contact", "tok-1"))
            self.assertEqual(api.calls[2], ("create", "1072"))
            self.assertEqual(api.calls[3][0], "attach")
            self.assertEqual(base64.b64decode(api.calls[3][2]), JPEG)
            state = json.loads((Path(tmp) / "fp-1.json").read_text())
            self.assertEqual(state["status"], "complete")
            self.assertEqual(state["case"]["CaseNumberInCRM"], "2610-1")

    def test_replay_returns_already_submitted_without_api_calls(self):
        with TemporaryDirectory() as tmp:
            self.run_submit(tmp, FakeAPI())
            api = FakeAPI()
            result = self.run_submit(tmp, api)
            self.assertEqual(result["status"], "already_submitted")
            self.assertEqual(api.calls, [])

    def test_attach_failure_needs_explicit_retry_and_no_recreate(self):
        with TemporaryDirectory() as tmp:
            api = FakeAPI(fail_attach_once=True)
            with self.assertRaises(ReviewNeeded):
                self.run_submit(tmp, api)
            # Without --retry-photo the engine refuses to continue.
            with self.assertRaises(ReviewNeeded):
                self.run_submit(tmp, FakeAPI())
            # With it, the same case is reused: no second create call.
            api2 = FakeAPI()
            result = self.run_submit(tmp, api2, retry_photo=True)
            self.assertEqual(result["status"], "submitted")
            self.assertNotIn(("create", "1072"), api2.calls)
            self.assertEqual(result["case"]["CaseIdInCRM"], "c-1")

    def test_create_uncertainty_is_terminal(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ReviewNeeded):
                self.run_submit(tmp, FakeAPI(fail_create=True))
            state = json.loads((Path(tmp) / "fp-1.json").read_text())
            self.assertEqual(state["status"], "creating")
            # The state machine never retries a case whose creation is uncertain.
            with self.assertRaises(ReviewNeeded):
                self.run_submit(tmp, FakeAPI(), retry_photo=True)

    def test_missing_case_reference_is_uncertain(self):
        class BadCreate(FakeAPI):
            def create(self, token, contact, report):
                return {"Success": True}
        with TemporaryDirectory() as tmp, self.assertRaises(ReviewNeeded):
            self.run_submit(tmp, BadCreate())

    def test_concurrent_run_is_refused(self):
        from filelock import FileLock
        with TemporaryDirectory() as tmp:
            lock = FileLock(str(Path(tmp) / "fp-1.json") + ".lock")
            lock.acquire(timeout=1)
            try:
                with self.assertRaisesRegex(ReviewNeeded, "another local process"):
                    self.run_submit(tmp, FakeAPI())
            finally:
                lock.release()


if __name__ == "__main__":
    unittest.main()
