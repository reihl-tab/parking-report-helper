"""report.html interface text comes from one catalog (td423/td424).

The catalog (src/parking_report/configs/report_strings.json) is the only
place that names a report language: one block per language with its code,
its own name, its text direction and whether it is a machine draft. These
tests discover the languages from the catalog and never from a literal
list, so adding a language is one new block plus one value per key.

The report's data stays as the city wrote it: category, street, address
and status names are never translated, and summary.json and the CSV files
are not touched by the report language.
"""

from __future__ import annotations

import argparse
import ast
import copy
import html
import io
import json
import re
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from parking_report import report_i18n, stats, stats_cli
from parking_report.stats_html import build_report

ROOT = Path(__file__).resolve().parents[1]
SCANNED_DIRS = ("src", "tests", "scripts", "tools")
# A line carrying this marker (or the line just above it) may name a catalog
# language code on purpose. Each use says why on the same line.
PIN_MARKER = "i18n-pin:"


def _catalog() -> dict:
    return json.loads(report_i18n.CATALOG_PATH.read_text(encoding="utf-8"))


def _codes() -> list[str]:
    return [language.code for language in report_i18n.languages()]


def _raw_codes() -> tuple[list[str], str]:
    """Codes and default read from the catalog file itself, not the loader.

    The parity tests use these so a gap fails with the key and language
    named, even though the loader refuses the whole catalog.
    """
    blocks = _catalog()["languages"]
    default = next(block["code"] for block in blocks if block["default"])
    return [block["code"] for block in blocks], default


def _summary() -> dict:
    return {
        "total_incidents": 13,
        "duplicates_skipped": 1,
        "date_range": {"first": "2026-05-01", "last": "2026-07-04"},
        "by_month": {"2026-05": 4, "2026-06": 9},
        "by_week": {"2026-W22": 4, "2026-W23": 9},
        "by_category": {"חניה אסורה - חניה על מדרכה": 9, stats.UNKNOWN: 4},
        "by_street": {"רחוב הדוגמה": 6, stats.NO_STREET: 1},
        "hotspots": {"רחוב הדוגמה 1": 6},
        "by_neighborhood": {"שכונת הדוגמה": 7, stats.OTHER_NEIGHBORHOOD: 6},
        "by_status": {"סיום טיפול": 9, "בטיפול": 4},
        "closure": {
            "closed_count": 9,
            "median_days": 0.5,
            "p75_days": 2.0,
            "max_days": 19.0,
            "buckets": {name: 1 for name, _, _ in stats.CLOSURE_BUCKETS},
            "median_days_by_category": {"חניה אסורה - חניה על מדרכה": 1.0},
        },
        "inputs": ["synthetic.json"],
    }


class CatalogShapeTest(unittest.TestCase):
    def test_shipped_catalog_is_valid(self):
        self.assertEqual(report_i18n.catalog_problems(_catalog()), [])

    def test_one_block_per_language_with_the_required_fields(self):
        blocks = _catalog()["languages"]
        self.assertGreaterEqual(len(blocks), 2)
        self.assertEqual(len({b["code"] for b in blocks}), len(blocks))
        self.assertEqual(sum(1 for b in blocks if b["default"]), 1)
        for block in blocks:
            with self.subTest(code=block["code"]):
                self.assertTrue(block["name"].strip())
                self.assertIn(block["dir"], ("rtl", "ltr"))
                self.assertIsInstance(block["machine_draft"], bool)
                # A machine draft says so in words, like parking-reporter-app.
                self.assertEqual("status" in block, block["machine_draft"])

    def test_default_stays_hebrew_so_existing_reports_are_unchanged(self):
        default = report_i18n.default_language()
        # i18n-pin: the default report.html stays Hebrew, as before the catalog
        self.assertEqual(default.code, "he")
        self.assertEqual(default.direction, "rtl")
        self.assertFalse(default.machine_draft)

    def test_every_non_default_language_is_a_machine_draft_until_reviewed(self):
        for language in report_i18n.languages():
            if not language.default:
                with self.subTest(code=language.code):
                    self.assertTrue(language.machine_draft)


class KeyParityTest(unittest.TestCase):
    def test_every_language_has_every_key_with_the_same_placeholders(self):
        data = _catalog()
        codes, default = _raw_codes()
        for key, values in data["strings"].items():
            expected = report_i18n.placeholders(values[default])
            for code in codes:
                with self.subTest(key=key, code=code):
                    self.assertIn(code, values)
                    self.assertTrue(values[code].strip())
                    self.assertEqual(report_i18n.placeholders(values[code]), expected)

    def test_strings_name_only_catalog_languages(self):
        codes = set(_raw_codes()[0])
        for key, values in _catalog()["strings"].items():
            with self.subTest(key=key):
                self.assertEqual(set(values), codes)

    def test_every_key_used_by_the_report_exists(self):
        keys = set(_catalog()["strings"])
        for key in report_i18n.REQUIRED_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, keys)

    def test_problems_are_reported_for_each_kind_of_gap(self):
        good = _catalog()
        default = report_i18n.default_language().code
        other = next(c for c in _codes() if c != default)
        key = next(iter(good["strings"]))

        missing = copy.deepcopy(good)
        del missing["strings"][key][other]
        empty = copy.deepcopy(good)
        empty["strings"][key][other] = " "
        stray = copy.deepcopy(good)
        stray["strings"][key]["zz-test"] = "x"
        params = copy.deepcopy(good)
        params["strings"]["overview.date_range_value"][other] = "{first}"
        two_defaults = copy.deepcopy(good)
        for block in two_defaults["languages"]:
            block["default"] = True
        bad_dir = copy.deepcopy(good)
        bad_dir["languages"][0]["dir"] = "up"
        duplicate = copy.deepcopy(good)
        duplicate["languages"].append(dict(duplicate["languages"][0], default=False))
        no_status = copy.deepcopy(good)
        for block in no_status["languages"]:
            block.pop("status", None)
            block["machine_draft"] = True
        stray_status = copy.deepcopy(good)
        for block in stray_status["languages"]:
            block["machine_draft"] = False
            block["status"] = "x"
        unknown_field = copy.deepcopy(good)
        unknown_field["languages"][0]["colour"] = "x"
        no_name = copy.deepcopy(good)
        no_name["languages"][0]["name"] = ""
        no_code = copy.deepcopy(good)
        no_code["languages"][0]["code"] = 7
        not_bool = copy.deepcopy(good)
        not_bool["languages"][0]["default"] = "yes"
        not_bool["languages"][0]["machine_draft"] = "no"
        value_not_object = copy.deepcopy(good)
        value_not_object["strings"][key] = "x"
        strings_not_object = copy.deepcopy(good)
        strings_not_object["strings"] = []
        for name, broken in (
            ("missing", missing),
            ("empty", empty),
            ("stray", stray),
            ("placeholders", params),
            ("two defaults", two_defaults),
            ("bad dir", bad_dir),
            ("duplicate code", duplicate),
            ("draft without status", no_status),
            ("status without draft", stray_status),
            ("unknown field", unknown_field),
            ("no name", no_name),
            ("no code", no_code),
            ("not a bool", not_bool),
            ("value not an object", value_not_object),
            ("strings not an object", strings_not_object),
            ("not an object", []),
            ("no languages", {"languages": [], "strings": {}}),
            ("block not an object", {"languages": ["x"], "strings": {}}),
        ):
            with self.subTest(name=name):
                self.assertNotEqual(report_i18n.catalog_problems(broken), [])

    def test_an_unreadable_catalog_is_refused_on_load(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "strings.json"
            path.write_text("{not json", encoding="utf-8")
            with patch.object(report_i18n, "CATALOG_PATH", path):
                with self.assertRaises(report_i18n.CatalogError):
                    report_i18n.languages()
            with patch.object(report_i18n, "CATALOG_PATH", Path(tmp) / "missing"):
                with self.assertRaises(report_i18n.CatalogError):
                    report_i18n.languages()

    def test_an_invalid_catalog_is_refused_on_load(self):
        broken = _catalog()
        del broken["strings"]["report.title"]
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "strings.json"
            path.write_text(json.dumps(broken), encoding="utf-8")
            with patch.object(report_i18n, "CATALOG_PATH", path):
                with self.assertRaises(report_i18n.CatalogError):
                    report_i18n.languages()


class DiscoveryTest(unittest.TestCase):
    """A language added to the catalog alone reaches every surface."""

    def _with_extra_language(self, tmp: str) -> tuple[Path, str]:
        data = _catalog()
        default = report_i18n.default_language().code
        code = "zz-test"
        data["languages"].append(
            {
                "code": code,
                "name": "Zedtest",
                "dir": "ltr",
                "default": False,
                "machine_draft": True,
                "status": "machine-draft, needs native review",
            }
        )
        for values in data["strings"].values():
            values[code] = "ZZ " + values[default]
        path = Path(tmp) / "strings.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path, code

    def test_languages_come_from_the_catalog_in_catalog_order(self):
        self.assertEqual(_codes(), [b["code"] for b in _catalog()["languages"]])
        names = [language.name for language in report_i18n.languages()]
        self.assertEqual(names, [b["name"] for b in _catalog()["languages"]])

    def test_a_new_catalog_block_needs_no_other_change(self):
        with TemporaryDirectory() as tmp:
            path, code = self._with_extra_language(tmp)
            with patch.object(report_i18n, "CATALOG_PATH", path):
                self.assertIn(code, [lang.code for lang in report_i18n.languages()])
                page = build_report(_summary(), lang=code)
                self.assertIn(f'<html lang="{code}" dir="ltr">', page)
                self.assertIn("ZZ ", page)
                out = Path(tmp) / "out"
                export = Path(tmp) / "export.json"
                export.write_text(json.dumps({"incidents": []}), encoding="utf-8")
                with redirect_stderr(io.StringIO()):
                    rc = stats_cli.run(
                        ["build", str(export), "-o", str(out), "--lang", code]
                    )
                self.assertEqual(rc, 0)
                written = (out / "report.html").read_text(encoding="utf-8")
                self.assertIn(f'lang="{code}"', written)


class LanguageOptionTest(unittest.TestCase):
    """--lang offers exactly the catalog's languages, nothing more."""

    def test_every_catalog_language_is_accepted(self):
        parser = stats_cli.build_parser()
        for code in _codes():
            for command in (["build", "x.json"], ["refresh"]):
                with self.subTest(code=code, command=command[0]):
                    args = parser.parse_args([*command, "-o", "d", "--lang", code])
                    self.assertEqual(args.lang, code)

    def test_default_is_the_catalog_default(self):
        args = stats_cli.build_parser().parse_args(["build", "x.json", "-o", "d"])
        self.assertIsNone(args.lang)
        self.assertEqual(
            build_report(_summary(), lang=args.lang),
            build_report(_summary(), lang=report_i18n.default_language().code),
        )

    def test_unknown_code_lists_exactly_the_catalog_languages(self):
        with self.assertRaises(argparse.ArgumentTypeError) as caught:
            report_i18n.language_code("zz-unknown")
        listed = re.findall(r"(\S+) \(([^)]+)\)", str(caught.exception))
        expected = [(lang.code, lang.name) for lang in report_i18n.languages()]
        self.assertEqual(listed, expected)

    def test_unknown_code_exits_2_before_anything_runs(self):
        err = io.StringIO()
        with TemporaryDirectory() as tmp, redirect_stderr(err):
            with self.assertRaises(SystemExit) as caught:
                stats_cli.run(["build", "missing.json", "-o", tmp, "--lang", "zz-x"])
            self.assertEqual(list(Path(tmp).iterdir()), [])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("--lang", err.getvalue())


class LocalizedReportTest(unittest.TestCase):
    def test_each_language_renders_its_own_text_and_direction(self):
        strings = _catalog()["strings"]
        formatted = {"report.generated", "overview.date_range_value"}
        notes = {"report.machine_draft_note", "report.city_data_note"}
        for language in report_i18n.languages():
            code = language.code
            with self.subTest(code=code):
                page = build_report(_summary(), lang=code)
                self.assertIn(f'<html lang="{code}" dir="{language.direction}">', page)
                self.assertIn(f"direction: {language.direction};", page)
                for key, values in strings.items():
                    if key in formatted | notes | {"chart.no_data"}:
                        continue
                    self.assertIn(html.escape(values[code]), page, key)
                self.assertIn(
                    html.escape(
                        report_i18n.text(
                            "report.generated", code, inputs="synthetic.json"
                        )
                    ),
                    page,
                )
                self.assertIn(
                    report_i18n.text(
                        "overview.date_range_value",
                        code,
                        first="2026-05-01",
                        last="2026-07-04",
                    ),
                    page,
                )
                # No hotspots: the empty chart shows the catalog's no-data text.
                empty = build_report(dict(_summary(), hotspots={}), lang=code)
                self.assertIn(strings["chart.no_data"][code], empty)

    def test_machine_draft_and_city_data_notes(self):
        data = _catalog()["strings"]
        for language in report_i18n.languages():
            with self.subTest(code=language.code):
                page = build_report(_summary(), lang=language.code)
                draft = data["report.machine_draft_note"][language.code]
                city = data["report.city_data_note"][language.code]
                self.assertEqual(draft in page, language.machine_draft)
                self.assertEqual(city in page, not language.default)

    def test_city_data_is_never_translated(self):
        summary = _summary()
        for language in report_i18n.languages():
            with self.subTest(code=language.code):
                page = build_report(summary, lang=language.code)
                for name in (
                    "חניה אסורה - חניה על מדרכה",
                    "רחוב הדוגמה",
                    "רחוב הדוגמה 1",
                    "שכונת הדוגמה",
                    "סיום טיפול",
                    "בטיפול",
                ):
                    self.assertIn(name, page)

    def test_summary_keys_stay_hebrew(self):
        summary = _summary()
        before = copy.deepcopy(summary)
        for language in report_i18n.languages():
            build_report(summary, lang=language.code)
        self.assertEqual(summary, before)

    def test_default_language_values_match_the_summary_labels(self):
        # report.html in the default language is byte-for-byte what it was
        # before the catalog: its labels equal the constants in stats.py.
        default = report_i18n.default_language().code
        labels = report_i18n.display_labels(default)
        self.assertEqual(
            [name for name, _, _ in stats.CLOSURE_BUCKETS],
            [labels[name] for name, _, _ in stats.CLOSURE_BUCKETS],
        )
        for constant in (stats.NO_STREET, stats.UNKNOWN, stats.OTHER_NEIGHBORHOOD):
            self.assertEqual(labels[constant], constant)

    def test_unknown_key_and_language_are_errors(self):
        with self.assertRaises(KeyError):
            report_i18n.text("no.such.key")
        with self.assertRaises(ValueError):
            report_i18n.language("zz-unknown")


def _string_constants(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value


def hardcoded_language_codes(roots: list[Path], codes: list[str]) -> list[str]:
    """Python string literals naming a catalog language, outside pinned lines."""
    attribute = re.compile(
        r"\b(?:xml:lang|hreflang|lang)\s*=\s*[\"']?(%s)\b"
        % "|".join(re.escape(code) for code in codes)
    )
    found = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            lines = path.read_text(encoding="utf-8").splitlines()
            for lineno, value in _string_constants(path):
                if value not in codes and not attribute.search(value):
                    continue
                here = lines[lineno - 1]
                above = lines[lineno - 2] if lineno > 1 else ""
                pinned_above = above.lstrip().startswith("#") and PIN_MARKER in above
                if PIN_MARKER in here or pinned_above:
                    continue
                found.append(f"{path.relative_to(ROOT)}:{lineno}: {value!r}")
    return found


class HardcodedLanguageScanTest(unittest.TestCase):
    def test_no_language_code_outside_the_catalog(self):
        roots = [ROOT / name for name in SCANNED_DIRS if (ROOT / name).is_dir()]
        found = hardcoded_language_codes(roots, _codes())
        self.assertEqual(
            found,
            [],
            "language codes belong in src/parking_report/configs/"
            "report_strings.json; a deliberate pin needs an "
            f"'{PIN_MARKER} <reason>' comment",
        )

    def test_scan_catches_a_stray_code(self):
        code = _codes()[-1]
        with TemporaryDirectory() as tmp:
            sample = Path(tmp) / "sample.py"
            with patch(f"{__name__}.ROOT", Path(tmp)):
                sample.write_text(f"X = {code!r}\n", encoding="utf-8")
                self.assertEqual(len(hardcoded_language_codes([Path(tmp)], [code])), 1)
                sample.write_text(f"X = '<p lang=\"{code}\">'\n", encoding="utf-8")
                self.assertEqual(len(hardcoded_language_codes([Path(tmp)], [code])), 1)
                sample.write_text(
                    f"X = {code!r}  # {PIN_MARKER} why\n", encoding="utf-8"
                )
                self.assertEqual(hardcoded_language_codes([Path(tmp)], [code]), [])


if __name__ == "__main__":
    unittest.main()
