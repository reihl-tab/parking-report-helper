"""Offline protocol tests. Never contact the municipality."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from parking_report import api106
except ImportError:
    api106 = None


@unittest.skipIf(api106 is None, "install the api extra")
class ProtocolTests(unittest.TestCase):
    def test_complete_and_duplicate(self):
        class Fake:
            def __init__(self):
                self.calls = []

            def login(self):
                self.calls.append("login")
                return "fake-token"

            def contact(self, token, reporter):
                self.calls.append("contact")
                return {}

            def create(self, token, contact, report):
                self.calls.append("create")
                return {"CaseIdInCRM": "fake-case", "WebCaseIdInCRM": "fake-web"}

            def attach(self, token, case, jpeg, filename):
                self.calls.append("attach")

        with tempfile.TemporaryDirectory() as tmp:
            fake = Fake()
            out = api106.submit(fake, {}, {}, b"jpeg", "a" * 64, Path(tmp))
            self.assertEqual(out["status"], "submitted")
            self.assertEqual(fake.calls, ["login", "contact", "create", "attach"])
            self.assertEqual(
                api106.submit(fake, {}, {}, b"jpeg", "a" * 64, Path(tmp))["status"],
                "already_submitted",
            )
            self.assertEqual(len(fake.calls), 4)
            self.assertEqual(
                (Path(tmp) / ("a" * 64 + ".json")).stat().st_mode & 0o777, 0o600
            )

    def test_uncertain_creation_blocks_retry(self):
        class Fake:
            def login(self):
                return "fake-token"

            def contact(self, *args):
                return {}

            def create(self, *args):
                raise OSError("timeout")

        with tempfile.TemporaryDirectory() as tmp:
            for _ in range(2):
                with self.assertRaises(api106.ReviewNeeded):
                    api106.submit(Fake(), {}, {}, b"jpeg", "b" * 64, Path(tmp))
            self.assertEqual(
                json.loads((Path(tmp) / ("b" * 64 + ".json")).read_text())["status"],
                "creating",
            )

    def test_state_persistence_failure_prevents_create(self):
        class Fake:
            def login(self):
                return "fake-token"

            def contact(self, *args):
                return {}

            def create(self, *args):
                raise AssertionError("must not create")

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {"PARKING_STATE_GIT": "1"}),
            patch.object(
                api106.subprocess, "run", return_value=argparse.Namespace(returncode=1)
            ),
            self.assertRaises(api106.ReviewNeeded),
        ):
            api106.submit(Fake(), {}, {}, b"jpeg", "c" * 64, Path(tmp))

    def test_redirect_is_not_followed(self):
        self.assertIsNone(
            api106.NoRedirect().redirect_request(
                None, None, 302, "", None, "https://example.invalid"
            )
        )

    def test_functional_preview_and_live_gate(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            Image.new("RGB", (10, 10), "white").save(folder / "photo.jpg")
            record = {
                "schema_version": 1,
                "status": "ready",
                "photo": "photo.jpg",
                "plate": "0000000",
                "captured_at": "2026-01-01 12:00",
                "address": {"street": "רחוב לדוגמה", "house_number": "1"},
                "violation": {"description": "חוסם מדרכה"},
            }
            (folder / "report.json").write_text(json.dumps(record))
            (folder / "addresses.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "streets": [
                            {"StreetCode": "fake-code", "StreetName": "רחוב לדוגמה"}
                        ],
                        "addresses": [[31.77, 35.21, "1", "רחוב לדוגמה"]],
                    }
                )
            )
            env = dict(
                os.environ,
                PARKING_REPORTER_FIRST_NAME="שם",
                PARKING_REPORTER_LAST_NAME="לדוגמה",
                PARKING_REPORTER_PHONE="0500000000",
                PARKING_REPORTER_ID_NUMBER="000000018",
                PARKING_REPORTER_EMAIL="example@example.invalid",
            )
            cmd = [
                sys.executable,
                "-m",
                "parking_report.api_cli",
                str(folder),
                "--addresses",
                str(folder / "addresses.json"),
            ]
            out = subprocess.run(
                cmd, env=env, capture_output=True, text=True, check=False
            )
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            data = json.loads(out.stdout)
            self.assertEqual(data["network_requests"], 0)
            self.assertNotIn("000000018", out.stdout)
            self.assertFalse((folder / ".106-state").exists())
            out = subprocess.run(
                cmd + ["--submit", "--approved-sha256", data["sha256"]],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(out.returncode, 4)
            self.assertNotIn("fake-code", out.stdout)


if __name__ == "__main__":
    unittest.main()
