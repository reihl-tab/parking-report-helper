#!/usr/bin/env python3
"""Generate the man pages for the shipped console commands (#188).

Single source: the machine-readable block in docs/CLI-CONTRACT.md (flags,
defaults, choices, exit codes; already checked against the real parsers by
tests/test_cli_contract.py) plus each command's own argparse definitions
(description, option help, metavars), read in-process. Help capture never
parses a real command line, reads configuration, binds a socket or touches
the network: parking-report uses build_parser(), parking-report-106 stops
inside main() at parse_args before anything else runs, and parking-report-http
reuses the landed _help_text() path.

Usage:
  python scripts/gen_manpages.py            write man/man1/*.1
  python scripts/gen_manpages.py --check    exit 1 if a committed page drifted
  python scripts/gen_manpages.py --output DIR

Opt-in diagnostics (OFF by default): --debug or PARKING_MANPAGES_DEBUG=1
prints stage names to stderr. Logs never contain arguments, paths, environment
values or configuration; a failure prints only the stage and the error class.
Stdlib only.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
import textwrap
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "CLI-CONTRACT.md"
PYPROJECT = ROOT / "pyproject.toml"
DEFAULT_OUTPUT = ROOT / "man" / "man1"
DEBUG_ENV = "PARKING_MANPAGES_DEBUG"
LOG_NAME = "gen_manpages"

# Fixed so output is reproducible; bump together with the package version.
PAGE_DATE = "2026-11-01"
MANUAL = "User Commands"
REQUIRED_SECTIONS = (
    "NAME",
    "SYNOPSIS",
    "DESCRIPTION",
    "OPTIONS",
    "EXIT STATUS",
    "SEE ALSO",
)

# Prose for options (and positionals, keyed by dest) whose argparse
# definition has no help string. Used only
# where the parser has none; a test fails on a stale or shadowing entry.
OPTION_NOTES: dict[str, dict[str, str]] = {
    "parking-report-106": {
        "folder": "report folder holding report.json and its photo.",
        "--addresses": "JSON file with the private address data used to "
        "prepare the report.",
        "--state-dir": "directory for local run state.",
        "--submit": "submit to the 106 API instead of the offline preview. "
        "Live submission also needs PARKING_106_ENABLE_LIVE=true, the exact "
        "--approved-sha256 and --ledger-dir; otherwise the run ends blocked.",
        "--approved-sha256": "the reviewed sha256 digest printed by the "
        "preview; must match exactly for a live submission.",
        "--retry-photo": "retry the photo attachment of an already "
        "submitted case; needs explicit submission approval.",
        "--approved-followup-sha256": "digest approval for a parent-linked "
        "follow-up manifest; must equal the reviewed digest.",
        "--approved-shared-photo-sha256": "digest approval for a shared-photo "
        "manifest (two distinct cars in one original photo); must equal the "
        "reviewed digest.",
        "--approved-shared-issue-sha256": "digest approval for a shared-issue "
        "manifest (two distinct non-vehicle issues grounded in one original "
        "photo); must equal the reviewed digest.",
        "--approved-extension-sha256": "digest approval for a shared-photo "
        "extension (one new car in a completed intake's unchanged original "
        "photo); must equal the reviewed digest.",
        "--approved-supersedes-sha256": "digest approval for a supersedes "
        "manifest (a corrected re-filing of a still-open intake on the same "
        "original photo); must equal the reviewed digest.",
    },
}

# Environment read by commands whose contract entry lists none. Names only;
# a test checks each name still appears in the command's source modules.
ENVIRONMENT: dict[str, list[tuple[str, str]]] = {
    "parking-report": [
        (
            "PARKING_DRIVE_TOKEN",
            "read-only Google Drive access token, used only with --drive-folder.",
        ),
    ],
    "parking-report-106": [
        (
            "PARKING_REPORTER_FIRST_NAME, PARKING_REPORTER_LAST_NAME, "
            "PARKING_REPORTER_PHONE, PARKING_REPORTER_ID_NUMBER, "
            "PARKING_REPORTER_EMAIL",
            "private reporter details, all required; values are never printed.",
        ),
        (
            "PARKING_106_ENABLE_LIVE",
            "must be exactly true for a live submission.",
        ),
        (
            "PARKING_106_SUBSCRIPTION_KEY, PARKING_106_USERNAME, PARKING_106_PASSWORD",
            "private 106 API credentials, needed only for a live submission.",
        ),
        (
            "PARKING_STATE_GIT",
            "legacy state-commit hook; unnecessary with --ledger-dir.",
        ),
    ],
}

# Argument rules enforced in code (parser.error, exit 2), not visible in the
# argparse definitions.
USAGE_NOTES: dict[str, str] = {
    "p106-stats": "The build subcommand takes one or more export JSON files "
    "and writes summary.json, the monthly/categories/streets CSVs and "
    "report.html into --output. Fully offline: no network, no credentials.",
    "parking-report": "Give exactly one of a cars JSON file (input) or --intake "
    "DIR. --drive-folder requires --intake; --reporter and --json require "
    "--municipality.",
}

SOURCE_MODULES: dict[str, list[str]] = {
    "parking-report": ["parking_report.cli", "parking_report.drive"],
    "p106-stats": ["parking_report.stats", "parking_report.stats_cli"],
    "parking-report-106": ["parking_report.api_cli", "parking_report.api106"],
    "parking-report-http": ["parking_report.http_service"],
}


class Log:
    """Stage diagnostics on stderr, OFF unless enabled. Never logs values."""

    def __init__(self, enabled: bool, name: str = LOG_NAME) -> None:
        self.enabled = enabled
        self.name = name
        self.stage = "start"

    def at(self, stage: str, command: str | None = None) -> None:
        self.stage = stage
        if self.enabled:
            extra = f" command={command}" if command else ""
            sys.stderr.write(f"{self.name}: stage={stage}{extra}\n")

    def failed(self, exc: BaseException) -> None:
        sys.stderr.write(
            f"{self.name}: failed at stage={self.stage} error={type(exc).__name__}\n"
        )


def debug_enabled(flag: bool, env: Mapping[str, str]) -> bool:
    return flag or env.get(DEBUG_ENV, "") == "1"


# --- inventory ---------------------------------------------------------------


def load_contract(path: Path | None = None) -> dict[str, Any]:
    text = (path or CONTRACT).read_text(encoding="utf-8")
    match = re.search(
        r"<!-- cli-contract:begin -->\s*```json\n(.*?)```\s*<!-- cli-contract:end -->",
        text,
        re.S,
    )
    if match is None:
        raise ValueError("contract block not found")
    data: dict[str, Any] = json.loads(match.group(1))
    return data


def shipped_commands(pyproject: Path | None = None) -> dict[str, str]:
    """Console scripts the package installs: name -> "module:function".

    Read from pyproject [project.scripts] in a checkout, else from the
    installed distribution's entry points (the Docker test image has no
    pyproject). These are the shipped commands; everything under scripts/
    and any `python -m` module entry is internal and gets no page.
    """
    pyproject = pyproject or PYPROJECT
    if pyproject.exists():
        import tomllib

        with pyproject.open("rb") as handle:
            scripts = tomllib.load(handle)["project"]["scripts"]
        return {str(k): str(v) for k, v in sorted(scripts.items())}
    from importlib.metadata import entry_points

    found = {
        ep.name: ep.value
        for ep in entry_points(group="console_scripts")
        if ep.value.startswith("parking_report.")
    }
    if not found:
        raise LookupError("no installed console scripts")
    return dict(sorted(found.items()))


def check_inventory(contract: dict[str, Any], shipped: dict[str, str]) -> None:
    commands = contract["commands"]
    if set(commands) != set(shipped):
        raise ValueError("shipped commands and contract commands differ")
    for name, target in shipped.items():
        if commands[name]["module"] != target:
            raise ValueError("contract module differs from the entry point")


# --- in-process parser capture -------------------------------------------------


class _Captured(Exception):
    def __init__(self, parser: argparse.ArgumentParser) -> None:
        super().__init__("captured")
        self.parser = parser


@contextmanager
def _intercept(method: str) -> Iterator[None]:
    original = getattr(argparse.ArgumentParser, method)

    def stop(self: argparse.ArgumentParser, *args: Any, **kwargs: Any) -> Any:
        raise _Captured(self)

    setattr(argparse.ArgumentParser, method, stop)
    try:
        yield
    finally:
        setattr(argparse.ArgumentParser, method, original)


def _capture(call: Callable[[], Any], method: str) -> argparse.ArgumentParser:
    with _intercept(method):
        try:
            call()
        except _Captured as captured:
            return captured.parser
    raise RuntimeError("parser was not captured")


def capture_parser(name: str, target: str) -> argparse.ArgumentParser:
    """The command's real parser, without running the command."""
    module_name, _, _ = target.partition(":")
    module = importlib.import_module(module_name)
    if name in ("parking-report", "p106-stats"):
        parser: argparse.ArgumentParser = module.build_parser()
        return parser
    if name == "parking-report-106":
        # main() builds its parser inline; stop at parse_args, before any
        # environment, file or network access.
        return _capture(lambda: module.main([]), "parse_args")
    if name == "parking-report-http":
        # The landed #188 help path; never main() without --help.
        return _capture(module._help_text, "format_help")
    raise ValueError("unknown command")


# --- roff rendering -------------------------------------------------------------


def esc(text: str) -> str:
    """Escape plain text for roff; marks `code` spans bold."""
    text = text.replace("\\", "\\e").replace("-", "\\-")
    text = re.sub(r"`([^`]+)`", r"\\fB\1\\fR", text)
    lines = []
    for line in text.split("\n"):
        if line.startswith((".", "'")):
            line = "\\&" + line
        lines.append(line)
    return "\n".join(lines)


def para(text: str) -> str:
    """Escaped text, filled to short source lines (roff refills them)."""
    wrapped = textwrap.wrap(
        " ".join(text.split()),
        width=70,
        break_long_words=False,
        break_on_hyphens=False,
    )
    return "\n".join(esc(line) for line in wrapped)


def _help(action: argparse.Action) -> str | None:
    if not action.help or action.help == argparse.SUPPRESS:
        return None
    return str(action.help).replace("%%", "%")


def _metavar(action: argparse.Action) -> str | None:
    if action.nargs == 0:
        return None
    if action.metavar is not None:
        return str(action.metavar)
    if action.choices is not None:
        return "{" + ",".join(str(c) for c in action.choices) + "}"
    return str(action.dest).upper()


def _visible(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    return [a for a in parser._actions if a.help != argparse.SUPPRESS]


def _first_sentence(text: str) -> str:
    text = " ".join(text.split())
    head = text.split(". ")[0].rstrip(".")
    return head[:1].lower() + head[1:] if head[:2] != head[:2].upper() else head


def _option_label(action: argparse.Action) -> str:
    names = ", ".join(f"\\fB{esc(o)}\\fR" for o in action.option_strings)
    meta = _metavar(action)
    return f"{names} \\fI{esc(meta)}\\fR" if meta else names


def _synopsis(name: str, parser: argparse.ArgumentParser) -> list[str]:
    out = [f".B {esc(name)}"]
    for action in _visible(parser):
        if action.option_strings:
            flag = f"\\fB{esc(action.option_strings[0])}\\fR"
            meta = _metavar(action)
            # "\\ " is an unbreakable space: a flag stays with its metavar.
            item = f"{flag}\\ \\fI{esc(meta)}\\fR" if meta else flag
            out.append(item if action.required else f"[{item}]")
    for action in _visible(parser):
        if not action.option_strings:
            item = f"\\fI{esc(str(action.metavar or action.dest))}\\fR"
            out.append(f"[{item}]" if action.nargs == "?" else item)
    return out


def _sentence(text: str) -> str:
    """Capitalise and terminate one help fragment."""
    text = text.strip()
    if not text:
        return ""
    text = text[:1].upper() + text[1:]
    return text if text.endswith((".", ")")) else text + "."


def _default_note(spec: dict[str, Any], help_text: str = "") -> str:
    parts = []
    if spec.get("required"):
        parts.append("Required.")
    if spec.get("choices"):
        parts.append("One of: " + ", ".join(spec["choices"]) + ".")
    default = spec.get("default")
    if default not in (None, "", False):
        parts.append(f"Default: {default}.")
    if spec.get("deprecated") and "deprecated" not in help_text.lower():
        parts.append("Deprecated; kept for compatibility.")
    return " ".join(parts)


def _http_epilog(parser: argparse.ArgumentParser) -> tuple[dict[str, str], str]:
    """Split the landed help epilog into env descriptions and a note."""
    env: dict[str, str] = {}
    note: list[str] = []
    for line in (parser.epilog or "").splitlines():
        m = re.match(r"^  (\S+)\s{2,}(.+)$", line)
        if m:
            env[m.group(1)] = m.group(2)
        elif line.strip() and line.strip() != "environment:":
            note.append(line.strip())
    return env, " ".join(note)


def _header_lines(name: str, version: str, description: str) -> list[str]:
    return [
        '.\\" Generated by scripts/gen_manpages.py from docs/CLI-CONTRACT.md',
        ".\\\" and the command's argparse definitions (#188). Do not edit.",
        f'.TH {esc(name.upper())} 1 "{PAGE_DATE}" '
        f'"parking\\-report\\-helper {esc(version)}" "{MANUAL}"',
        ".nh",
        ".ad l",
        ".SH NAME",
        para(f"{name} - {_first_sentence(description)}"),
    ]


def _http_environment(
    spec: dict[str, Any], parser: argparse.ArgumentParser
) -> tuple[list[tuple[str, str]], str]:
    """The HTTP command's environment rows (contract order) and epilog note."""
    env_help, note = _http_epilog(parser)
    missing = [e for e in spec.get("environment", []) if e not in env_help]
    if missing:
        raise ValueError("contract environment missing from help epilog")
    return [(e, env_help[e]) for e in spec["environment"]], note


def _description_notes(
    name: str, spec: dict[str, Any], parser: argparse.ArgumentParser
) -> tuple[list[str], list[tuple[str, str]]]:
    """Paragraphs after DESCRIPTION, and the ENVIRONMENT rows for the page."""
    lines: list[str] = []
    if name in USAGE_NOTES:
        lines += [".PP", para(USAGE_NOTES[name])]
    env_lines: list[tuple[str, str]] = list(ENVIRONMENT.get(name, []))
    if name == "parking-report-http":
        env_lines, note = _http_environment(spec, parser)
        lines += [".PP", para(note)]
    if spec.get("json_output"):
        lines += [".PP", para("JSON output: " + str(spec["json_output"]) + ".")]
    return lines, env_lines


def _option_entry(action: argparse.Action, body: str) -> list[str]:
    return [".TP", _option_label(action), para(body)]


def _positional_entry(action: argparse.Action, text: str) -> list[str]:
    label = esc(str(action.metavar or action.dest))
    return [".TP", f"\\fI{label}\\fR", para(_sentence(text))]


def _top_options(
    parser: argparse.ArgumentParser, spec: dict[str, Any], notes: dict[str, str]
) -> list[str]:
    """OPTIONS: help text, else OPTION_NOTES; contract notes by long name."""
    lines: list[str] = []
    options = spec.get("options", {})
    for action in _visible(parser):
        if not action.option_strings:
            continue
        text = _help(action) or notes.get(action.option_strings[-1], "")
        long_name = next((o for o in action.option_strings if o.startswith("--")), None)
        spec_opt = options.get(long_name, {}) if long_name else {}
        extra = _default_note(spec_opt, text)
        body = " ".join(x for x in (_sentence(text), extra) if x)
        lines += _option_entry(action, body)
    return lines


def _top_positionals(
    parser: argparse.ArgumentParser, notes: dict[str, str]
) -> list[str]:
    positionals = [a for a in _visible(parser) if not a.option_strings]
    if not positionals:
        return []
    lines = [".SS Arguments"]
    for action in positionals:
        text = _help(action) or notes.get(str(action.dest), "")
        lines += _positional_entry(action, text)
    return lines


def _contract_key(action: argparse.Action) -> str:
    """The long option name, else the first option string."""
    return next(
        (o for o in action.option_strings if o.startswith("--")),
        action.option_strings[0],
    )


def _subcommand_lines(
    sub_name: str, sub_parser: argparse.ArgumentParser, spec: dict[str, Any]
) -> list[str]:
    """One sub-command's options then positionals (help text only, no notes)."""
    lines = [f'.SS "{esc(sub_name)} options"']
    sub_options = spec.get("subcommands", {}).get(sub_name, {}).get("options", {})
    visible = _visible(sub_parser)
    for action in (a for a in visible if a.option_strings):
        text = _sentence(_help(action) or "")
        extra = _default_note(sub_options.get(_contract_key(action), {}), text)
        body = " ".join(x for x in (text, extra) if x)
        lines += _option_entry(action, body)
    for action in (a for a in visible if not a.option_strings):
        lines += _positional_entry(action, _help(action) or "")
    return lines


def _subcommands(parser: argparse.ArgumentParser, spec: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for sub_action in parser._actions:
        if not isinstance(sub_action, argparse._SubParsersAction):
            continue
        for sub_name, sub_parser in sub_action.choices.items():
            lines += _subcommand_lines(sub_name, sub_parser, spec)
    return lines


def _environment_section(env_lines: list[tuple[str, str]]) -> list[str]:
    if not env_lines:
        return []
    lines = [".SH ENVIRONMENT"]
    for env_names, text in env_lines:
        # One tag per variable; .TQ stacks tags sharing one description.
        for i, env_name in enumerate(env_names.split(", ")):
            lines += [".TQ" if i else ".TP", f"\\fB{esc(env_name)}\\fR"]
        lines.append(para(_sentence(text)))
    return lines


def _closing_sections(
    name: str, spec: dict[str, Any], commands: Sequence[str]
) -> list[str]:
    """EXIT STATUS (numeric order) and SEE ALSO (the other commands)."""
    lines = [".SH EXIT STATUS"]
    for code, text in sorted(spec["exit_codes"].items(), key=lambda kv: int(kv[0])):
        lines += [".TP", f".B {esc(code)}", para(_sentence(text))]
    see = ",\n".join(f".BR {esc(c)} (1)" for c in commands if c != name)
    return lines + [
        ".SH SEE ALSO",
        see,
        ".PP",
        para("docs/CLI-CONTRACT.md in the source distribution."),
    ]


def render_page(
    name: str,
    spec: dict[str, Any],
    parser: argparse.ArgumentParser,
    version: str,
    commands: Sequence[str],
) -> str:
    description = str(parser.description or "").strip()
    notes = OPTION_NOTES.get(name, {})
    lines = [
        *_header_lines(name, version, description),
        ".SH SYNOPSIS",
        *_synopsis(name, parser),
        ".SH DESCRIPTION",
        para(description),
    ]
    notes_lines, env_lines = _description_notes(name, spec, parser)
    lines += [
        *notes_lines,
        ".SH OPTIONS",
        *_top_options(parser, spec, notes),
        *_top_positionals(parser, notes),
        *_subcommands(parser, spec),
        *_environment_section(env_lines),
        *_closing_sections(name, spec, commands),
    ]
    return "\n".join(lines) + "\n"


def generate(log: Log) -> dict[str, str]:
    """Return {filename: page text} for every shipped command."""
    log.at("load_contract")
    contract = load_contract()
    log.at("inventory")
    shipped = shipped_commands()
    check_inventory(contract, shipped)
    log.at("version")
    from parking_report import __version__

    if contract["package_version"] != __version__:
        raise ValueError("contract and package versions differ")
    pages: dict[str, str] = {}
    for name, target in shipped.items():
        log.at("capture", name)
        parser = capture_parser(name, target)
        log.at("render", name)
        pages[f"{name}.1"] = render_page(
            name, contract["commands"][name], parser, __version__, list(shipped)
        )
    return pages


def write_pages(pages: dict[str, str], output: Path, log: Log) -> None:
    log.at("write")
    output.mkdir(parents=True, exist_ok=True)
    for filename, text in pages.items():
        (output / filename).write_bytes(text.encode("utf-8"))


def drifted(pages: dict[str, str], output: Path) -> list[str]:
    """Page names whose committed bytes differ, plus stray .1 files."""
    bad = []
    for filename, text in pages.items():
        path = output / filename
        if not path.exists() or path.read_bytes() != text.encode("utf-8"):
            bad.append(filename)
    if output.is_dir():
        bad += sorted(p.name for p in output.glob("*.1") if p.name not in pages)
    return bad


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gen_manpages.py", description="Generate man/man1 pages (#188)."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="exit 1 if pages drifted")
    parser.add_argument(
        "--debug", action="store_true", help=f"stage diagnostics (or {DEBUG_ENV}=1)"
    )
    args = parser.parse_args(argv)
    log = Log(debug_enabled(args.debug, os.environ))
    try:
        pages = generate(log)
        if args.check:
            log.at("check")
            bad = drifted(pages, args.output)
            for filename in bad:
                sys.stderr.write(f"{LOG_NAME}: drift in {filename}\n")
            if bad:
                sys.stderr.write(
                    f"{LOG_NAME}: run python scripts/gen_manpages.py to regenerate\n"
                )
                return 1
            print(f"{len(pages)} man pages up to date")
            return 0
        write_pages(pages, args.output, log)
    except Exception as exc:  # noqa: BLE001 - stage and class only, no text
        log.failed(exc)
        return 1
    log.at("done")
    print(f"wrote {len(pages)} man pages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
