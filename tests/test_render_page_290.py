"""gen_manpages.render_page() with sub-command options and arguments that
have no help text (#290).

Before the fix a sub-command option or positional without `help` reached
`_sentence(None)` and render_page crashed with AttributeError. Missing help
now renders like the top-level options and positionals already do: an empty
description line, or only the contract note (Required., Default: ...) when
there is one. help=argparse.SUPPRESS stays hidden. Options with help render
unchanged (the #288 characterization fixtures pin that). Synthetic parser and
names only; nothing is run.
"""

import argparse
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "gen_manpages.py"
spec = importlib.util.spec_from_file_location("gen_manpages_290", SCRIPT)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

NAME = "demo-nohelp"
SPEC = {
    "subcommands": {"build": {"options": {"--out": {"required": True}}}},
    "exit_codes": {"0": "ok"},
}


def nohelp_parser():
    parser = argparse.ArgumentParser(prog=NAME, description="no-help demo")
    sub = parser.add_subparsers(dest="action")
    build = sub.add_parser("build")
    build.add_argument("--plain")
    build.add_argument("--empty", help="")
    build.add_argument("--out", required=True)
    build.add_argument("--hidden", help=argparse.SUPPRESS)
    build.add_argument("--named", help="named flag")
    build.add_argument("src")
    return parser


def render():
    return g.render_page(NAME, SPEC, nohelp_parser(), "9.8.7", [NAME])


def section(page, title):
    lines = page.splitlines()
    start = lines.index(f'.SS "{title}"')
    end = next(
        i for i in range(start + 1, len(lines)) if lines[i].startswith((".SS", ".SH"))
    )
    return lines[start:end]


class SubcommandWithoutHelpTests(unittest.TestCase):
    def test_renders_without_crashing(self):
        self.assertIn('.SS "build options"', render())

    def test_missing_help_renders_an_empty_description(self):
        self.assertEqual(
            section(render(), "build options"),
            [
                '.SS "build options"',
                ".TP",
                "\\fB\\-h\\fR, \\fB\\-\\-help\\fR",
                "Show this help message and exit.",
                ".TP",
                "\\fB\\-\\-plain\\fR \\fIPLAIN\\fR",
                "",
                ".TP",
                "\\fB\\-\\-empty\\fR \\fIEMPTY\\fR",
                "",
                ".TP",
                "\\fB\\-\\-out\\fR \\fIOUT\\fR",
                "Required.",
                ".TP",
                "\\fB\\-\\-named\\fR \\fINAMED\\fR",
                "Named flag.",
                ".TP",
                "\\fIsrc\\fR",
                "",
            ],
        )

    def test_suppressed_option_stays_hidden(self):
        self.assertNotIn("hidden", render())

    def test_matches_top_level_rendering_of_missing_help(self):
        # The top-level path already renders a help-less positional this way.
        page = render()
        self.assertIn(".TP\n\\fIaction\\fR\n\n", page)
        self.assertIn(".TP\n\\fIsrc\\fR\n\n", page)


if __name__ == "__main__":
    unittest.main()
