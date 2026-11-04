"""`p106-stats --neighborhoods PATH`: wire a street -> neighborhood mapping (#225).

Synthetic street and neighborhood names only.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from parking_report.stats_cli import run

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "stats"
EXPORTS = [str(FIXTURES / "export_a.json"), str(FIXTURES / "export_b.json")]

AREA_A = "שכונת דוגמה"
AREA_B = "שכונת בדיקה"
MAPPING = {"רחוב הדוגמה": AREA_A, "שדרות הבדיקה": AREA_B}
SECTION = "<h2>דיווחים לפי שכונה</h2>"

UNREADABLE = "p106-stats: --neighborhoods: the mapping file could not be read\n"
INVALID = (
    "p106-stats: --neighborhoods: the mapping file must be one JSON object "
    "mapping non-empty street names to non-empty neighborhood names\n"
)
# Planted in every invalid file; it must never reach stderr.
SENTINEL = "SENTINEL-CONTENT-7f3a"


def _write(path: Path, mapping: object) -> Path:
    path.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    return path


def _summary(out: Path) -> dict:
    return json.loads((out / "summary.json").read_text(encoding="utf-8"))


class BuildNeighborhoodsTest(unittest.TestCase):
    def _build(self, tmp: Path, name: str, *extra: str) -> tuple[int, Path]:
        out = tmp / name
        return run(["build", *EXPORTS, "-o", str(out), *extra]), out

    def test_mapping_produces_by_neighborhood_counts(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            mapping = _write(tmp / "areas.json", MAPPING)
            code, out = self._build(tmp, "out", "--neighborhoods", str(mapping))
            self.assertEqual(code, 0)
            # 13 incidents: 6 on the first street, 3 on the second, and the
            # unmapped street (3) plus the streetless row (1) fall into "(אחר)".
            self.assertEqual(
                list(_summary(out)["by_neighborhood"].items()),
                [(AREA_A, 6), ("(אחר)", 4), (AREA_B, 3)],
            )

    def test_html_gains_neighborhood_section(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            mapping = _write(tmp / "areas.json", MAPPING)
            code, out = self._build(tmp, "out", "--neighborhoods", str(mapping))
            self.assertEqual(code, 0)
            html = (out / "report.html").read_text(encoding="utf-8")
            self.assertIn(SECTION, html)
            self.assertIn(AREA_A, html)

    def test_without_flag_output_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            mapping = _write(tmp / "areas.json", MAPPING)
            code_plain, plain = self._build(tmp, "plain")
            code_mapped, mapped = self._build(
                tmp, "mapped", "--neighborhoods", str(mapping)
            )
            self.assertEqual((code_plain, code_mapped), (0, 0))
            plain_summary = _summary(plain)
            self.assertIsNone(plain_summary["by_neighborhood"])
            mapped_summary = _summary(mapped)
            mapped_summary["by_neighborhood"] = None
            self.assertEqual(plain_summary, mapped_summary)
            self.assertEqual(list(plain_summary), list(mapped_summary))
            for name in ("monthly.csv", "categories.csv", "streets.csv"):
                self.assertEqual(
                    (plain / name).read_bytes(), (mapped / name).read_bytes(), name
                )
            html = (plain / "report.html").read_text(encoding="utf-8")
            self.assertNotIn(SECTION, html)


def _invalid_files(tmp: Path) -> list[tuple[str, Path, str]]:
    """(label, path, expected stderr) for every rejected mapping file."""
    cases: list[tuple[str, object]] = [
        ("list", [SENTINEL]),
        ("string", SENTINEL),
        ("number", 7),
        ("null", None),
        ("empty key", {"": SENTINEL}),
        ("blank key", {"   ": SENTINEL}),
        ("empty value", {SENTINEL: ""}),
        ("blank value", {SENTINEL: " "}),
        ("number value", {SENTINEL: 3}),
        ("null value", {SENTINEL: None}),
        ("list value", {SENTINEL: [AREA_A]}),
        ("object value", {SENTINEL: {"x": AREA_A}}),
    ]
    files = [
        (label, _write(tmp / f"case{i}.json", value), INVALID)
        for i, (label, value) in enumerate(cases)
    ]
    not_json = tmp / "not-json.json"
    not_json.write_text("{" + SENTINEL, encoding="utf-8")
    files.append(("not json", not_json, INVALID))
    not_utf8 = tmp / "latin1.json"
    not_utf8.write_bytes(('{"' + SENTINEL + '": "\xe9"}').encode("latin-1"))
    files.append(("not utf-8", not_utf8, UNREADABLE))
    files.append(("missing", tmp / "no-such-file.json", UNREADABLE))
    folder = tmp / "a-directory"
    folder.mkdir()
    files.append(("directory", folder, UNREADABLE))
    return files


class InvalidMappingTest(unittest.TestCase):
    def _assert_rejected(self, argv: list[str], path: Path, expected: str) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = run(argv)
        self.assertEqual(code, 1)
        self.assertEqual(stderr.getvalue(), expected)
        self.assertNotIn(SENTINEL, stderr.getvalue())
        self.assertNotIn(path.name, stderr.getvalue())

    def test_build_rejects_invalid_mapping_with_fixed_message(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            for index, (label, path, expected) in enumerate(_invalid_files(tmp)):
                with self.subTest(label):
                    out = tmp / f"out-{index}"
                    argv = ["build", *EXPORTS, "-o", str(out)]
                    self._assert_rejected(
                        [*argv, "--neighborhoods", str(path)], path, expected
                    )
                    self.assertFalse(out.exists())

    def test_refresh_rejects_invalid_mapping_before_fetch(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            for index, (label, path, expected) in enumerate(_invalid_files(tmp)):
                with self.subTest(label):
                    out = tmp / f"out-{index}"
                    union = tmp / f"union-{index}.json"
                    with (
                        mock.patch.dict("os.environ", RefreshNeighborhoodsTest.ENV),
                        mock.patch(
                            "parking_report.stats_cli._fetch_incidents",
                            side_effect=AssertionError("fetch must not run"),
                        ) as fetch,
                    ):
                        self._assert_rejected(
                            [
                                "refresh",
                                "-o",
                                str(out),
                                "--save-union",
                                str(union),
                                "--neighborhoods",
                                str(path),
                            ],
                            path,
                            expected,
                        )
                    fetch.assert_not_called()
                    self.assertFalse(out.exists())
                    self.assertFalse(union.exists())

    def test_empty_object_is_accepted(self):
        # An empty mapping is still one JSON object: every incident is "(אחר)".
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            mapping = _write(tmp / "areas.json", {})
            out = tmp / "out"
            code = run(
                ["build", *EXPORTS, "-o", str(out), "--neighborhoods", str(mapping)]
            )
            self.assertEqual(code, 0)
            self.assertEqual(_summary(out)["by_neighborhood"], {"(אחר)": 13})


class RefreshNeighborhoodsTest(unittest.TestCase):
    ENV = {
        "PARKING_106_SUBSCRIPTION_KEY": "synthetic-key",
        "PARKING_106_USERNAME": "synthetic-user",
        "PARKING_106_PASSWORD": "synthetic-pass",
        "PARKING_REPORTER_ID_NUMBER": "000000000",
    }

    def _inc(self, case_number: str, street: str) -> dict:
        return {
            "case_number": case_number,
            "createdon": "2026-05-02T08:00:00",
            "streetname": street,
            "housenumber": "2",
            "subjectname": "חניה אסורה - חניה על מדרכה",
            "statusname": "בטיפול",
        }

    def _refresh(self, tmp: Path, *extra: str) -> tuple[int, dict]:
        fetched = [
            self._inc("2000-900001", "רחוב הדוגמה"),
            self._inc("2000-900002", "רחוב הדוגמה"),
            self._inc("2000-900003", "שדרות הבדיקה"),
            self._inc("2000-900004", "רחוב לא ממופה"),
        ]
        out = tmp / "out"
        with (
            mock.patch.dict("os.environ", self.ENV),
            mock.patch(
                "parking_report.stats_cli._fetch_incidents", return_value=fetched
            ),
        ):
            code = run(["refresh", "-o", str(out), *extra])
        return code, _summary(out) if code == 0 else {}

    def test_refresh_passes_mapping_to_summary(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            mapping = _write(tmp / "areas.json", MAPPING)
            code, summary = self._refresh(tmp, "--neighborhoods", str(mapping))
            self.assertEqual(code, 0)
            self.assertEqual(
                list(summary["by_neighborhood"].items()),
                [(AREA_A, 2), ("(אחר)", 1), (AREA_B, 1)],
            )

    def test_refresh_without_flag_keeps_null(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            code, summary = self._refresh(Path(tmp_name))
            self.assertEqual(code, 0)
            self.assertIsNone(summary["by_neighborhood"])


if __name__ == "__main__":
    unittest.main()
