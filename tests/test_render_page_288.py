"""Characterization of gen_manpages.render_page() on synthetic parsers (#288).

`scripts/gen_manpages.py --check` pins the four shipped pages byte for byte;
this pins the branches those pages do not reach (sub-command positionals,
short-only sub-command options, deprecated/required/choices notes, stacked
environment tags, the HTTP epilog path and its missing-variable error). The
expected pages under tests/fixtures/manpage_288/ were rendered before the
#288 refactor. Synthetic parsers and names only; nothing is run.
"""

import argparse
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "gen_manpages.py"
EXPECTED = Path(__file__).resolve().parent / "fixtures" / "manpage_288"
spec = importlib.util.spec_from_file_location("gen_manpages_288", SCRIPT)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

COMMANDS = ["demo-cmd", "demo-sub", "parking-report-http"]
EXIT_CODES = {"2": "usage error", "0": "success", "10": "blocked"}


def demo_parser():
    parser = argparse.ArgumentParser(
        prog="demo-cmd",
        description="Demo command for tests. Second sentence with `code` here.",
    )
    parser.add_argument("folder", help=None)
    parser.add_argument(
        "extra", nargs="?", metavar="EXTRA", help="optional 100%% extra"
    )
    parser.add_argument("--mode", choices=["a", "b"], required=True, help="the mode")
    parser.add_argument("--count", type=int, default=3, help="how many (deprecated)")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true", help="be chatty.")
    parser.add_argument("--old", help="old flag")
    parser.add_argument("--hidden", help=argparse.SUPPRESS)
    parser.add_argument("-x", help="short only")
    return parser


DEMO_SPEC = {
    "json_output": "a single object on stdout",
    "options": {
        "--mode": {"required": True, "choices": ["a", "b"]},
        "--count": {"default": 3, "deprecated": True},
        "--old": {"deprecated": True, "default": ""},
        "--quiet": {"default": False},
    },
    "exit_codes": EXIT_CODES,
}


def sub_parser():
    parser = argparse.ArgumentParser(prog="demo-sub", description="sub demo")
    parser.add_argument("--top", help="top flag")
    sub = parser.add_subparsers(dest="action")
    build = sub.add_parser("build")
    build.add_argument("inputs", nargs="+", help="input files")
    build.add_argument("--output", required=True, help="output dir")
    build.add_argument("-k", help="short only option")
    build.add_argument("--legacy", help="legacy flag")
    build.add_argument("--quiet", action="store_true", help="say nothing")
    refresh = sub.add_parser("refresh")
    refresh.add_argument("--since", default="2026-01-01", help="start date")
    refresh.add_argument("target", metavar="DIR", help="target dir")
    return parser


SUB_SPEC = {
    "subcommands": {
        "build": {
            "options": {
                "--output": {"required": True},
                "-k": {"choices": ["1", "2"]},
                "--legacy": {"deprecated": True},
            }
        },
        "refresh": {"options": {"--since": {"default": "2026-01-01"}}},
    },
    "exit_codes": {"0": "ok", "1": "failed"},
}


def http_parser():
    parser = argparse.ArgumentParser(
        prog="parking-report-http",
        description="HTTP demo. Serves things.",
        epilog="environment:\n  DEMO_PORT    port to bind\n  DEMO_TOKEN   the token\n\nNote line one.\nNote line two.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind host")
    return parser


HTTP_SPEC = {
    "environment": ["DEMO_TOKEN", "DEMO_PORT"],
    "exit_codes": {"0": "ok"},
}

NOTES = {"demo-cmd": {"folder": "the report folder.", "--quiet": "no output."}}
ENVIRONMENT = {
    "demo-cmd": [
        ("DEMO_A, DEMO_B, DEMO_C", "three stacked variables."),
        ("DEMO_D", "one variable"),
    ],
}
USAGE = {"demo-sub": "Usage rule enforced in code."}

CASES = {
    "demo-cmd": (demo_parser, DEMO_SPEC),
    "demo-sub": (sub_parser, SUB_SPEC),
    "parking-report-http": (http_parser, HTTP_SPEC),
}


def render(name):
    make, spec_ = CASES[name]
    with (
        patch.dict(g.OPTION_NOTES, NOTES, clear=True),
        patch.dict(g.ENVIRONMENT, ENVIRONMENT, clear=True),
        patch.dict(g.USAGE_NOTES, USAGE, clear=True),
    ):
        return g.render_page(name, spec_, make(), "9.8.7", COMMANDS)


def expected(name):
    return (EXPECTED / f"{name}.roff").read_bytes().decode("utf-8")


class RenderPageCharacterizationTests(unittest.TestCase):
    def test_expected_files_match_cases(self):
        self.assertEqual(sorted(p.stem for p in EXPECTED.glob("*.roff")), sorted(CASES))

    def test_synthetic_pages_are_byte_identical(self):
        for name in CASES:
            with self.subTest(command=name):
                self.assertEqual(render(name), expected(name))

    def test_http_environment_missing_from_epilog_is_an_error(self):
        spec_ = {**HTTP_SPEC, "environment": ["DEMO_TOKEN", "DEMO_ABSENT"]}
        with self.assertRaisesRegex(ValueError, "contract environment missing"):
            g.render_page("parking-report-http", spec_, http_parser(), "1", COMMANDS)


if __name__ == "__main__":
    unittest.main()
