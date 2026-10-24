"""Intake: report folders (report.json + photo) to cars, locally and via a fake Drive server."""

import http.server
import json
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path

from support import FIXTURES, run_cli

from parking_report.drive import check_api, download_folder
from parking_report.intake import read_intake, record_to_car

INTAKE = FIXTURES / "intake"


class TestReadIntake(unittest.TestCase):
    def test_ready_record_becomes_car_and_others_are_skipped_with_reason(self):
        found = read_intake(INTAKE)
        self.assertEqual(len(found.cars), 1)
        car = found.cars[0]
        self.assertEqual(car["street"], "התנופה 17")
        self.assertEqual(car["datetime"], "2026-10-05 10:05")
        self.assertIn("על המדרכה וחוסם אותה למעבר הולכי רגל", car["notes"])
        self.assertTrue(found.photos[0].endswith("photo.jpg"))
        reasons = dict(found.skipped)
        self.assertIn("מעבר החציה", reasons["2026-10-05_1130_cd34"])
        self.assertEqual(reasons["2026-10-05_1200_ef56"], "plate is missing")

    def test_missing_house_number_requires_clarification(self):
        record = json.loads(
            (INTAKE / "2026-10-05_1005_ab12" / "report.json").read_text()
        )
        record["address"]["house_number"] = ""
        with self.assertRaisesRegex(ValueError, "address.house_number is missing"):
            record_to_car(record)
        record["address"]["house_number"] = "40"
        record["address"]["approximate"] = True
        self.assertIn("40", record_to_car(record)["street"])

    def test_location_description_is_in_draft_without_coordinates(self):
        record = json.loads(
            (INTAKE / "2026-10-05_1005_ab12" / "report.json").read_text()
        )
        record["location_description"] = "ליד כניסת הבניין על ריצוף האבן"
        car = record_to_car(record)
        self.assertIn(record["location_description"], car["notes"])
        self.assertNotIn(str(record["gps"]["lat"]), car["notes"])
        self.assertNotIn(str(record["gps"]["lon"]), car["notes"])
        self.assertIn("קירוב", car["notes"])

    def test_registry_text_is_added_to_parking_reports(self):
        record = json.loads(
            (INTAKE / "2026-10-05_1005_ab12" / "report.json").read_text()
        )
        self.assertNotIn("registry_text", record_to_car(record))
        record["registry"] = {
            "plate": "1234567",
            "checked_on": "2026-10-08",
            "vehicle_lookup": "not_found_in_dataset",
            "disability_lookup": "not_found_in_dataset",
        }
        car = record_to_car(record)
        self.assertNotIn("רישיון", car["registry_text"])
        self.assertIn("לא נמצא לרכב תג נכה", car["registry_text"])

    def test_bad_inputs(self):
        with self.assertRaises(ValueError):
            read_intake(INTAKE / "nope")
        with self.assertRaises(ValueError):
            record_to_car({"schema_version": 2})
        with self.assertRaises(ValueError):
            record_to_car([])

    def test_folder_without_record_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "x").mkdir()
            found = read_intake(d)
        self.assertEqual(found.skipped, [("x", "no report.json")])


class TestIntakeCli(unittest.TestCase):
    def test_form_values_from_intake_never_guess_skipped(self):
        r = run_cli("--intake", str(INTAKE), "--municipality", "jerusalem")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("# "), 1)
        self.assertIn("התנופה", r.stdout)
        self.assertIn("5.10.2026, 10:05", r.stdout)
        self.assertIn("skipped 2026-10-05_1130_cd34", r.stderr)
        self.assertIn("car 1 photo:", r.stderr)
        self.assertNotIn("11-111-11", r.stdout)

    def test_plain_report_from_intake(self):
        r = run_cli("--intake", str(INTAKE), "--city", "ירושלים")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("00-000-00 (קופרה)", r.stdout)

    def test_usage_errors(self):
        for args in (
            ["--intake", str(INTAKE), "sample.json"],
            [],
            ["--drive-folder", "abcde12345"],
            [
                "sample.json",
                "--intake",
                str(INTAKE),
                "--drive-api",
                "http://localhost:1",
            ],
        ):
            self.assertEqual(run_cli(*args).returncode, 2, args)

    def test_missing_token_is_clear_error(self):
        with tempfile.TemporaryDirectory() as d:
            r = run_cli("--intake", d, "--drive-folder", "abcde12345")
        self.assertEqual(r.returncode, 1)
        self.assertIn("PARKING_DRIVE_TOKEN", r.stderr)


class FakeDrive:
    """Serves one root folder with one report subfolder. Records every request."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, str]] = []
        record = (INTAKE / "2026-10-05_1005_ab12" / "report.json").read_bytes()
        photo = b"fake-jpeg"
        drive = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                drive.requests.append(
                    (self.command, self.path, self.headers.get("Authorization", ""))
                )
                url = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(url.query)
                if url.path == "/files" and "'root1' in parents" in query["q"][0]:
                    body = {
                        "files": [
                            {
                                "id": "sub12345",
                                "name": "2026-10-05_1005_ab12",
                                "mimeType": "application/vnd.google-apps.folder",
                            }
                        ]
                    }
                elif url.path == "/files" and "'sub12345' in parents" in query["q"][0]:
                    body = {
                        "files": [
                            {
                                "id": "file1",
                                "name": "report.json",
                                "mimeType": "application/json",
                            },
                            {
                                "id": "file2",
                                "name": "photo.jpg",
                                "mimeType": "image/jpeg",
                            },
                            {
                                "id": "file3",
                                "name": "../evil.sh",
                                "mimeType": "text/plain",
                            },
                        ]
                    }
                elif url.path == "/files/file1":
                    return self._send(record)
                elif url.path == "/files/file2":
                    return self._send(photo)
                else:
                    return self._send(b"nope", 404)
                self._send(json.dumps(body).encode())

            def _send(self, data, status=200):
                self.send_response(status)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class TestDrive(unittest.TestCase):
    def test_download_is_read_only_and_only_expected_files(self):
        drive = FakeDrive()
        self.addCleanup(drive.close)
        with tempfile.TemporaryDirectory() as d:
            download_folder("root1", d, base=drive.url, token="tok")
            files = sorted(
                p.relative_to(d).as_posix() for p in Path(d).rglob("*") if p.is_file()
            )
            self.assertEqual(
                files,
                ["2026-10-05_1005_ab12/photo.jpg", "2026-10-05_1005_ab12/report.json"],
            )
            self.assertEqual(len(read_intake(d).cars), 1)
        self.assertTrue(
            all(m == "GET" and a == "Bearer tok" for m, _, a in drive.requests)
        )

    def test_cli_with_drive_folder_and_error_not_retried(self):
        drive = FakeDrive()
        self.addCleanup(drive.close)
        with tempfile.TemporaryDirectory() as d:
            r = run_cli(
                "--intake",
                d,
                "--drive-folder",
                "root1",
                "--drive-api",
                drive.url,
                "--municipality",
                "jerusalem",
            )
            self.assertEqual(r.returncode, 1)  # no token in the environment
        env_ok = {"PARKING_DRIVE_TOKEN": "tok"}
        import os
        import subprocess
        import sys

        from support import ROOT

        env = {
            **os.environ,
            **env_ok,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": str(ROOT / "src"),
        }
        with tempfile.TemporaryDirectory() as d:
            ok = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "parking_report",
                    "--intake",
                    d,
                    "--drive-folder",
                    "root1",
                    "--drive-api",
                    drive.url,
                    "--municipality",
                    "jerusalem",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=30,
            )
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertIn("התנופה", ok.stdout)
        before = len(drive.requests)
        with tempfile.TemporaryDirectory() as d:
            bad = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "parking_report",
                    "--intake",
                    d,
                    "--drive-folder",
                    "missing1",
                    "--drive-api",
                    drive.url,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=30,
            )
        self.assertEqual(bad.returncode, 1)
        self.assertEqual(len(drive.requests) - before, 1)

    def test_api_host_allowlist(self):
        self.assertEqual(
            check_api("https://www.googleapis.com/drive/v3"),
            "https://www.googleapis.com/drive/v3",
        )
        for url in (
            "https://evil.example/drive/v3",
            "http://www.googleapis.com/drive/v3",
        ):
            with self.assertRaises(ValueError):
                check_api(url)

    def test_bad_folder_id_rejected(self):
        with self.assertRaises(ValueError):
            download_folder("x' or 1=1", ".", token="t")


if __name__ == "__main__":
    unittest.main()
