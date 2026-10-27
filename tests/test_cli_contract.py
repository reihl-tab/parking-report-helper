"""docs/CLI-CONTRACT.md matches the real entry points (#73).

The machine-readable block in the doc is compared with the argparse parsers
(introspected, not parsed from --help text) and with the exit paths, so a
flag, default or exit-code change without a doc update fails here. Offline:
no network, no secrets; the HTTP service never binds a port.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from importlib import metadata
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from parking_report import api_cli, cli, http_service

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "CLI-CONTRACT.md"


def load_contract(text: str | None = None) -> dict:
    text = DOC.read_text(encoding="utf-8") if text is None else text
    match = re.search(
        r"<!-- cli-contract:begin -->\s*```json\n(.*?)\n```\s*<!-- cli-contract:end -->",
        text,
        re.DOTALL,
    )
    if not match:
        raise AssertionError(
            "cli-contract JSON block missing from docs/CLI-CONTRACT.md"
        )
    return json.loads(match.group(1))


class _Captured(Exception):
    def __init__(self, parser: argparse.ArgumentParser) -> None:
        self.parser = parser


def api_cli_parser() -> argparse.ArgumentParser:
    """api_cli builds its parser inside main(); capture it before parsing."""

    def capture(self, *_args, **_kwargs):
        raise _Captured(self)

    with patch.object(argparse.ArgumentParser, "parse_args", capture):
        try:
            api_cli.main([])
        except _Captured as captured:
            return captured.parser
    raise AssertionError("api_cli.main did not build a parser")


def describe(parser: argparse.ArgumentParser) -> tuple[list[str], dict]:
    positionals, options = [], {}
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        if not action.option_strings:
            positionals.append(action.dest)
            continue
        entry: dict = {"default": action.default}
        if isinstance(action, argparse._StoreTrueAction):
            entry["flag"] = True
        if action.required:
            entry["required"] = True
        if action.choices:
            entry["choices"] = list(action.choices)
        if action.help == argparse.SUPPRESS:
            entry["hidden"] = True
        elif (action.help or "").startswith("Deprecated"):
            # The contract's deprecation marker must match the parser's help,
            # so a flag can't silently lose (or gain) its deprecated status.
            entry["deprecated"] = True
        default = entry["default"]
        if isinstance(default, Path):
            entry["default"] = str(default)
        options[action.option_strings[0]] = entry
    return positionals, options


def documented(command: dict) -> tuple[list[str], dict]:
    return command["positionals"], command["options"]


def run(func, argv):
    out, err = io.StringIO(), io.StringIO()
    out.reconfigure = lambda **_k: None  # type: ignore[attr-defined]
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = func(argv)
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class ContractDocTest(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_contract()

    def test_entry_points_and_version_match_package(self) -> None:
        # Source checkout: pyproject.toml. Docker test stage (no pyproject in
        # /app): the installed distribution's metadata, built from that file.
        pyproject = ROOT / "pyproject.toml"
        if pyproject.exists():
            project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
            version, scripts = project["version"], project["scripts"]
        else:
            version = metadata.version("parking-report-helper")
            scripts = {
                ep.name: ep.value
                for ep in metadata.distribution("parking-report-helper").entry_points
                if ep.group == "console_scripts"
            }
        self.assertEqual(self.contract["package_version"], version)
        modules = {
            name: spec["module"] for name, spec in self.contract["commands"].items()
        }
        self.assertEqual(modules, scripts)

    def test_parking_report_options_match_parser(self) -> None:
        self.assertEqual(
            describe(cli.build_parser()),
            documented(self.contract["commands"]["parking-report"]),
        )

    def test_parking_report_106_options_match_parser(self) -> None:
        self.assertEqual(
            describe(api_cli_parser()),
            documented(self.contract["commands"]["parking-report-106"]),
        )

    def test_planted_doc_drift_is_detected(self) -> None:
        text = DOC.read_text(encoding="utf-8")
        for old, new in (
            ('"--city": {"default": ""}', '"--city": {"default": "x"}'),
            (
                '"--state-dir": {"default": ".106-state"}',
                '"--state-dir": {"default": "state"}',
            ),
            (
                '        "--json": {"default": false, "flag": true}\n',
                '        "--json": {"default": false}\n',
            ),
        ):
            self.assertIn(old, text)
            drifted = load_contract(text.replace(old, new, 1))
            self.assertNotEqual(
                (describe(cli.build_parser()), describe(api_cli_parser())),
                (
                    documented(drifted["commands"]["parking-report"]),
                    documented(drifted["commands"]["parking-report-106"]),
                ),
            )
        with self.assertRaises(AssertionError):
            load_contract(text.replace("<!-- cli-contract:end -->", ""))


class ExitCodeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_contract()["commands"]

    def test_parking_report_exit_codes(self) -> None:
        documented_codes = set(self.contract["parking-report"]["exit_codes"])
        self.assertEqual(documented_codes, {"0", "1", "2"})
        with TemporaryDirectory() as tmp:
            cars = Path(tmp) / "cars.json"
            cars.write_text("[]", encoding="utf-8")
            self.assertEqual(run(cli.main, [str(cars)])[0], 0)
            self.assertEqual(run(cli.main, [str(Path(tmp) / "missing.json")])[0], 1)
        self.assertEqual(run(cli.main, [])[0], 2)
        self.assertEqual(run(cli.main, ["cars.json", "--json"])[0], 2)
        self.assertEqual(run(cli.main, ["--no-such-flag"])[0], 2)

    def test_parking_report_106_exit_codes_and_fixed_blocked_line(self) -> None:
        spec = self.contract["parking-report-106"]
        self.assertEqual(set(spec["exit_codes"]), {"0", "2", "4"})
        self.assertEqual(run(api_cli.main, [])[0], 2)
        with TemporaryDirectory() as tmp, patch.dict("os.environ", {}, clear=True):
            code, out, err = run(
                api_cli.main, [tmp, "--addresses", str(Path(tmp) / "a.json")]
            )
        self.assertEqual(code, 4)
        line = json.loads(out)
        self.assertEqual(sorted(line), sorted(spec["stdout_keys"]["blocked"]))
        self.assertEqual(line["status"], "blocked")
        self.assertEqual(err, "")

    def test_parking_report_106_help_and_usage_are_argparse_text_not_json(
        self,
    ) -> None:
        # The one-JSON-line guarantee starts after argument parsing (#126 review).
        code, out, err = run(api_cli.main, ["--help"])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("usage: "))
        self.assertNotIn("{", out.splitlines()[0])
        self.assertEqual(err, "")
        code, out, err = run(api_cli.main, ["folder"])  # --addresses missing
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("usage: ", err)
        self.assertIn("--addresses", err)

    def test_parking_report_http_invalid_config_exits_with_fixed_message(self) -> None:
        spec = self.contract["parking-report-http"]
        self.assertEqual(set(spec["exit_codes"]), {"1"})
        cases = ({}, {"PARKING_HTTP_TOKEN": "x" * 32, "PORT": "70000"})
        for env in cases:
            with (
                self.subTest(env=sorted(env)),
                patch.dict("os.environ", env, clear=True),
                patch.object(http_service, "make_server") as server,
                self.assertRaises(SystemExit) as raised,
            ):
                http_service.main()
            server.assert_not_called()
            # A str exit code means exit status 1, with the message on stderr.
            self.assertIsInstance(raised.exception.code, str)
            self.assertEqual(raised.exception.code, spec["config_error_message"])

    def test_parking_report_http_ignores_extra_argv(self) -> None:
        spec = self.contract["parking-report-http"]
        self.assertEqual(spec["argv"], "ignored (not parsed, not rejected)")
        env = {"PARKING_HTTP_TOKEN": "z" * 32, "PORT": "10001"}
        argv = ["parking-report-http", "--no-such-flag", "extra"]
        with (
            patch.dict("os.environ", env, clear=True),
            patch("sys.argv", argv),
            patch.object(http_service, "make_server") as server,
        ):
            http_service.main()  # no SystemExit: the extra arguments are ignored
        server.assert_called_once()
        self.assertEqual(server.call_args.args[:2], ("0.0.0.0", 10001))
        server.return_value.__enter__.return_value.serve_forever.assert_called_once()


# Synthetic placeholder reporter values; prepare() is mocked and never reads them.
SYNTHETIC_REPORTER_ENV = {
    "PARKING_REPORTER_" + key.upper(): "synthetic"
    for key in ("first_name", "last_name", "phone", "id_number", "email")
}


class SuccessOutputTest(unittest.TestCase):
    """Synthetic success paths for parking-report-106 (#126 review, finding 3).

    prepare(), the photo read, the ledger and the municipal client are all
    mocked: no network, no port, no live account and no real submission.
    """

    DIGEST = "0" * 64

    def setUp(self) -> None:
        self.keys = load_contract()["commands"]["parking-report-106"]["stdout_keys"]
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name) / "report-1"
        self.folder.mkdir()
        (self.folder / "report.json").write_text(
            json.dumps({"photo": "p.jpg"}), encoding="utf-8"
        )
        prepared = ({}, {"CaseSubjectCode": "0"}, b"jpeg", self.DIGEST)
        for target, value in (
            ("parking_report.api_cli.prepare", prepared),
            ("parking_report.api106.read_picture", ("h", None, b"jpeg")),
        ):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _argv(self, *extra: str) -> list[str]:
        return [str(self.folder), "--addresses", "a.json", *extra]

    def test_dry_run_line_matches_contract(self) -> None:
        with patch.dict("os.environ", SYNTHETIC_REPORTER_ENV, clear=True):
            code, out, err = run(api_cli.main, self._argv())
        self.assertEqual(code, 0)
        line = json.loads(out)
        self.assertEqual(sorted(line), sorted(self.keys["dry_run"]))
        self.assertEqual(line["status"], "dry_run")
        self.assertEqual(line["network_requests"], 0)
        self.assertEqual(err, "")

    def test_submit_line_matches_contract(self) -> None:
        ledger = MagicMock()
        ledger.reserve.return_value = {"state": {}}
        env = dict(SYNTHETIC_REPORTER_ENV, PARKING_106_ENABLE_LIVE="true")
        with (
            patch.dict("os.environ", env, clear=True),
            patch("parking_report.ledger.GitLedger", return_value=ledger),
            patch("parking_report.api_cli.validate_owner_choice"),
            patch("parking_report.api106.Municipality") as municipality,
            patch(
                "parking_report.api106.submit",
                return_value={"status": "submitted", "photo_attached": True},
            ) as submit,
        ):
            code, out, err = run(
                api_cli.main,
                self._argv(
                    "--submit",
                    "--approved-sha256",
                    self.DIGEST,
                    "--ledger-dir",
                    self.tmp.name,
                ),
            )
        self.assertEqual(code, 0)
        submit.assert_called_once()
        self.assertIs(submit.call_args.args[0], municipality.return_value)
        line = json.loads(out)
        self.assertEqual(sorted(line), sorted(self.keys["submit"]))
        self.assertEqual(line["status"], "submitted")
        self.assertEqual(err, "")


if __name__ == "__main__":
    unittest.main()
