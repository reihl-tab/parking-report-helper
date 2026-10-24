"""In-process coverage of api_cli.main: argument validation, dry run, and blocked paths."""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from parking_report import api_cli

ENV = {
    "PARKING_REPORTER_FIRST_NAME": "דנה",
    "PARKING_REPORTER_LAST_NAME": "לוי",
    "PARKING_REPORTER_PHONE": "0500000000",
    "PARKING_REPORTER_ID_NUMBER": "000000018",
    "PARKING_REPORTER_EMAIL": "dana@example.com",
}


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
                "approved_description": "גרפיטי על קיר ברחוב לדוגמה 1. אבקש לטפל במפגע.",
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


def run(argv, env=ENV):
    out = Out()
    with patch.object(sys, "stdout", out), patch.dict("os.environ", env, clear=False):
        code = api_cli.main(argv)
    return code, out.getvalue()


class ApiCliMainTests(unittest.TestCase):
    def test_dry_run(self):
        with TemporaryDirectory() as tmp:
            folder, addresses = make_folder(tmp)
            code, out = run([str(folder), "--addresses", str(addresses)])
            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertEqual(payload["status"], "dry_run")
            self.assertEqual(payload["network_requests"], 0)
            self.assertTrue(payload["sha256"])
            self.assertGreater(payload["photo_upload_bytes"], 0)

    def test_submit_without_ledger_is_blocked(self):
        with TemporaryDirectory() as tmp:
            folder, addresses = make_folder(tmp)
            code, out = run([str(folder), "--addresses", str(addresses), "--submit"])
            self.assertEqual(code, 4)
            self.assertEqual(json.loads(out)["status"], "blocked")

    def test_retry_photo_without_submit_is_blocked(self):
        with TemporaryDirectory() as tmp:
            folder, addresses = make_folder(tmp)
            code, out = run(
                [str(folder), "--addresses", str(addresses), "--retry-photo"]
            )
            self.assertEqual(code, 4)
            self.assertEqual(json.loads(out)["status"], "blocked")

    def test_missing_report_is_blocked(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / "empty"
            folder.mkdir()
            code, out = run([str(folder), "--addresses", str(Path(tmp) / "a.json")])
            self.assertEqual(code, 4)

    def test_missing_reporter_env_is_blocked(self):
        with TemporaryDirectory() as tmp:
            folder, addresses = make_folder(tmp)
            env = {k: "" for k in ENV}
            code, out = run([str(folder), "--addresses", str(addresses)], env=env)
            self.assertEqual(code, 4)

    def test_corrupted_hebrew_description_is_blocked(self):
        with TemporaryDirectory() as tmp:
            folder, addresses = make_folder(tmp)
            record = json.loads((folder / "report.json").read_text(encoding="utf-8"))
            record["approved_description"] = "גרפיטי על קיר ברחוב".encode().decode("latin-1")
            (folder / "report.json").write_text(json.dumps(record), encoding="utf-8")
            code, out = run([str(folder), "--addresses", str(addresses)])
            self.assertEqual(code, 4)


if __name__ == "__main__":
    unittest.main()
