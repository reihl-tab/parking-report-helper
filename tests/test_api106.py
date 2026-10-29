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


@unittest.skipIf(api106 is None, "install the api extra")
class HebrewFinalLetterTests(unittest.TestCase):
    """Regression for #137: final-letter spelling variants must alias."""

    def test_normalize_folds_all_five_final_letters(self):
        for final, regular in zip("ךםןףץ", "כמנפצ"):
            with self.subTest(pair=(final, regular)):
                self.assertEqual(
                    api106.normalize(f"א{final}"), api106.normalize(f"א{regular}")
                )

    def test_normalize_matches_final_letter_street_variant(self):
        self.assertEqual(
            api106.normalize("רחוב משה ברוך"),
            api106.normalize("רחוב משה ברוכ"),
        )

    def test_fold_direction_is_final_to_regular(self):
        self.assertEqual(api106.normalize("אך"), "אכ")
        self.assertEqual(api106.normalize("אם"), "אמ")

    def test_fold_applies_to_every_occurrence(self):
        self.assertEqual(api106.normalize("ךכך"), "כככ")

    def test_nfkc_runs_before_folding(self):
        # U+FB26 HEBREW LETTER WIDE FINAL MEM: NFKC maps it to final mem,
        # which the fold then maps to regular mem.
        self.assertEqual(api106.normalize("\ufb26"), "מ")

    def test_qof_and_kaf_stay_distinct(self):
        self.assertNotEqual(api106.normalize("רייך"), api106.normalize("רייק"))

    def test_nfkc_and_alnum_filter_still_apply(self):
        self.assertEqual(api106.normalize("ＡＢＣ１２３"), "ABC123")
        self.assertEqual(api106.normalize("רחוב! הלך?"), "רחובהלכ")

    def _addresses(self, streets, addresses):
        data = {"version": 1, "streets": streets, "addresses": addresses}
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        with tmp:
            json.dump(data, tmp, ensure_ascii=False)
        self.addCleanup(Path(tmp.name).unlink)
        return api106.Addresses(Path(tmp.name))

    def test_resolve_accepts_final_letter_variant(self):
        addr = self._addresses(
            [
                {
                    "StreetCode": "99999",
                    "StreetName": "רחוב משה ברוך",
                    "CityName": "ירושלים",
                    "CityCode": "3000",
                }
            ],
            [],
        )
        resolved = addr.resolve("רחוב משה ברוכ", "1", (31.77, 35.21))
        self.assertEqual(resolved["street_code"], "99999")
        self.assertEqual(resolved["street_name"], "רחוב משה ברוך")

    def test_final_letter_ambiguity_stays_fail_closed(self):
        addr = self._addresses(
            [
                {"StreetCode": "1", "StreetName": "רחוב הלך", "CityName": "ירושלים"},
                {"StreetCode": "2", "StreetName": "רחוב הלכ", "CityName": "ירושלים"},
            ],
            [],
        )
        with self.assertRaises(api106.InputError):
            addr.resolve("רחוב הלך", "1", (31.77, 35.21))


@unittest.skipIf(api106 is None, "install the api extra")
class StreetAliasTests(unittest.TestCase):
    """Regression for #139: dataset aliases cover transliteration variants."""

    def _addresses(self, streets):
        data = {"version": 1, "streets": streets, "addresses": []}
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        with tmp:
            json.dump(data, tmp, ensure_ascii=False)
        self.addCleanup(Path(tmp.name).unlink)
        return api106.Addresses(Path(tmp.name))

    def test_alias_resolves_to_owning_street(self):
        addr = self._addresses(
            [
                {
                    "StreetCode": "838",
                    "StreetName": "מבוא חביבה רייק",
                    "CityName": "ירושלים",
                    "aliases": ["מבוא חביבה רייך"],
                }
            ]
        )
        resolved = addr.resolve("מבוא חביבה רייך", "1", (31.766717, 35.209297))
        self.assertEqual(resolved["street_code"], "838")
        self.assertEqual(resolved["street_name"], "מבוא חביבה רייק")

    def test_alias_collision_stays_fail_closed(self):
        addr = self._addresses(
            [
                {
                    "StreetCode": "1",
                    "StreetName": "רחוב הרצל",
                    "CityName": "ירושלים",
                    "aliases": ["רחוב וולפסון"],
                },
                {
                    "StreetCode": "2",
                    "StreetName": "רחוב וולפסון",
                    "CityName": "ירושלים",
                },
            ]
        )
        with self.assertRaises(api106.InputError):
            addr.resolve("רחוב וולפסון", "1", (31.766717, 35.209297))

    def test_dataset_without_aliases_is_unchanged(self):
        addr = self._addresses(
            [{"StreetCode": "1", "StreetName": "רחוב הרצל", "CityName": "ירושלים"}]
        )
        resolved = addr.resolve("רחוב הרצל", "1", (31.766717, 35.209297))
        self.assertEqual(resolved["street_code"], "1")

    def test_non_string_or_non_list_aliases_rejected(self):
        for bad in ("רחוב הרצל", [7], {}):
            with self.subTest(bad=bad):
                with self.assertRaises(api106.InputError):
                    self._addresses(
                        [
                            {
                                "StreetCode": "1",
                                "StreetName": "רחוב הרצל",
                                "CityName": "ירושלים",
                                "aliases": bad,
                            }
                        ]
                    )

    def test_alias_normalizing_to_empty_is_ignored(self):
        addr = self._addresses(
            [
                {
                    "StreetCode": "1",
                    "StreetName": "רחוב הרצל",
                    "CityName": "ירושלים",
                    "aliases": ["!!!"],
                }
            ]
        )
        resolved = addr.resolve("רחוב הרצל", "1", (31.77, 35.21))
        self.assertEqual(resolved["street_code"], "1")
        with self.assertRaises(api106.InputError):
            addr.resolve("!!!", "1", (31.77, 35.21))

    def test_house_point_with_ambiguous_street_gets_no_code(self):
        data = {
            "version": 1,
            "streets": [
                {
                    "StreetCode": "1",
                    "StreetName": "רחוב וולפסון",
                    "CityName": "ירושלים",
                },
                {
                    "StreetCode": "2",
                    "StreetName": "רחוב אחר",
                    "CityName": "ירושלים",
                    "aliases": ["רחוב וולפסון"],
                },
            ],
            "addresses": [[31.77, 35.21, "5", "רחוב וולפסון"]],
        }
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        with tmp:
            json.dump(data, tmp, ensure_ascii=False)
        self.addCleanup(Path(tmp.name).unlink)
        addr = api106.Addresses(Path(tmp.name))
        self.assertEqual(len(addr.points), 1)
        self.assertIsNone(addr.points[0][3])


if __name__ == "__main__":
    unittest.main()
