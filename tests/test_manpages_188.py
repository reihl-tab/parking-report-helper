"""Generated man pages for the shipped commands (#188): drift, content,
install layout and opt-in diagnostics. Offline and synthetic only: no
listener, no 106 call, no real configuration. The commands' --help output is
covered elsewhere (test_cli.py, test_api_cli_main.py, test_http_help_188.py).
"""

import contextlib
import importlib
import importlib.util
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
MAN1 = ROOT / "man" / "man1"
FAKE_SECRET = "synthetic-secret-188-do-not-log"
SHIPPED = {"p106-stats", "parking-report", "parking-report-106", "parking-report-http"}


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = load("gen_manpages")


def quiet_log():
    return gen.Log(False)


def synthetic_env(**extra):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PARKING_106_PASSWORD": FAKE_SECRET,
        "PARKING_HTTP_TOKEN": FAKE_SECRET,
        "PARKING_REPORTER_ID_NUMBER": FAKE_SECRET,
    }
    env.update(extra)
    return env


def run_script(name, *args, env):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / f"{name}.py"), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


class InventoryTests(unittest.TestCase):
    def test_shipped_commands_match_contract(self):
        shipped = gen.shipped_commands()
        contract = gen.load_contract()
        self.assertEqual(set(shipped), SHIPPED)
        gen.check_inventory(contract, shipped)

    def test_installed_entry_points_agree_with_pyproject(self):
        # The Docker test image has no pyproject.toml; the generator then reads
        # the installed distribution's console scripts.
        self.assertEqual(
            gen.shipped_commands(Path("/nonexistent/pyproject.toml")),
            gen.shipped_commands(),
        )

    def test_inventory_mismatch_is_an_error(self):
        contract = gen.load_contract()
        shipped = dict(gen.shipped_commands(), **{"parking-report-extra": "x:y"})
        with self.assertRaises(ValueError):
            gen.check_inventory(contract, shipped)

    def test_capture_runs_no_command(self):
        # The 106 capture stops at parse_args: no environment is read, so a
        # missing reporter config cannot matter and nothing is printed.
        out, err = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, {}, clear=True),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
            patch("socket.socket", side_effect=AssertionError("network")),
        ):
            for name, target in gen.shipped_commands().items():
                parser = gen.capture_parser(name, target)
                self.assertTrue(parser.description)
        self.assertEqual(out.getvalue() + err.getvalue(), "")
        parser_class = importlib.import_module("argparse").ArgumentParser
        self.assertEqual(parser_class.parse_args.__name__, "parse_args")
        self.assertEqual(parser_class.format_help.__name__, "format_help")


class PageContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages = gen.generate(quiet_log())
        cls.contract = gen.load_contract()

    def test_one_page_per_shipped_command(self):
        self.assertEqual(set(self.pages), {f"{c}.1" for c in SHIPPED})

    def test_required_sections_and_header(self):
        version = self.contract["package_version"]
        for filename, text in self.pages.items():
            with self.subTest(page=filename):
                th = next(line for line in text.splitlines() if line.startswith(".TH"))
                self.assertIn(f'"parking\\-report\\-helper {version}"', th)
                self.assertIn(f' 1 "{gen.PAGE_DATE}" ', th)
                sections = re.findall(r"^\.SH (.+)$", text, re.M)
                for section in gen.REQUIRED_SECTIONS:
                    self.assertIn(section, sections)

    def test_every_contract_option_and_exit_code_is_documented(self):
        for name, spec in self.contract["commands"].items():
            text = self.pages[f"{name}.1"]
            with self.subTest(command=name):
                for option, details in spec["options"].items():
                    escaped = gen.esc(option)
                    if details.get("hidden"):
                        self.assertNotIn(escaped, text)
                    else:
                        self.assertIn(f"\\fB{escaped}\\fR", text)
                exits = re.findall(
                    r"^\.B (\d+)$", text.split(".SH EXIT STATUS")[1], re.M
                )
                self.assertEqual(sorted(exits), sorted(spec["exit_codes"]))
                for env_name in spec.get("environment", []):
                    self.assertIn(f"\\fB{gen.esc(env_name)}\\fR", text)

    def test_documented_environment_is_read_by_the_command(self):
        for name, entries in gen.ENVIRONMENT.items():
            source = "".join(
                Path(importlib.import_module(m).__file__).read_text(encoding="utf-8")
                for m in gen.SOURCE_MODULES[name]
            )
            for names, _ in entries:
                for env_name in names.split(", "):
                    with self.subTest(env=env_name):
                        stem = env_name.removeprefix("PARKING_REPORTER_")
                        if stem != env_name:
                            self.assertIn("PARKING_REPORTER_", source)
                            self.assertIn(f'"{stem.lower()}"', source)
                        else:
                            self.assertIn(f'"{env_name}"', source)

    def test_option_notes_only_fill_missing_help(self):
        for name, notes in gen.OPTION_NOTES.items():
            parser = gen.capture_parser(name, gen.shipped_commands()[name])
            by_key = {}
            for action in parser._actions:
                by_key[action.dest] = action
                for option in action.option_strings:
                    by_key[option] = action
            for key in notes:
                with self.subTest(command=name, key=key):
                    self.assertIn(key, by_key)
                    self.assertIsNone(by_key[key].help)

    def test_generation_is_deterministic(self):
        with patch.dict(os.environ, {"COLUMNS": "40"}):
            again = gen.generate(quiet_log())
        self.assertEqual(again, self.pages)

    def test_roff_lines_are_safe(self):
        # Text lines never start with a control character; requests are known.
        request = re.compile(r"^(\.\\\"|\.[A-Za-z]{1,2}( |$))")
        bad = [
            (filename, line)
            for filename, text in self.pages.items()
            for line in text.splitlines()
            if line[:1] in (".", "'") and not request.match(line)
        ]
        self.assertEqual(bad, [])


@unittest.skipUnless(MAN1.is_dir(), "man/ is not copied into the Docker test image")
class DriftTests(unittest.TestCase):
    def test_committed_pages_match_the_generator(self):
        pages = gen.generate(quiet_log())
        self.assertEqual(
            gen.drifted(pages, MAN1),
            [],
            "man pages drifted; run python scripts/gen_manpages.py",
        )

    def test_check_mode_detects_an_edited_page(self):
        pages = gen.generate(quiet_log())
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            gen.write_pages(pages, out, quiet_log())
            self.assertEqual(gen.drifted(pages, out), [])
            page = out / "parking-report.1"
            page.write_bytes(page.read_bytes().replace(b"\\-\\-city", b"\\-\\-town"))
            (out / "internal.1").write_text("x", encoding="utf-8")
            self.assertEqual(
                gen.drifted(pages, out), ["parking-report.1", "internal.1"]
            )


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / "src-pages"
        gen.write_pages(gen.generate(quiet_log()), self.source, quiet_log())
        (self.source / "internal-tool.1").write_text("internal", encoding="utf-8")
        self.stage = Path(self.tmp.name) / f"stage-{FAKE_SECRET}"

    def run_install(self, *args, **env):
        return run_script(
            "install_manpages",
            "--source",
            str(self.source),
            *args,
            env=synthetic_env(DESTDIR=str(self.stage), **env),
        )

    def installed(self):
        return sorted(
            str(p.relative_to(self.stage)) for p in self.stage.rglob("*") if p.is_file()
        )

    def test_installs_exactly_the_shipped_pages_under_destdir_prefix(self):
        result = self.run_install(PREFIX="/opt/plh")
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = sorted(f"opt/plh/share/man/man1/{c}.1" for c in SHIPPED)
        manifest = "opt/plh/share/man/.parking-report-helper-man1.manifest"  # #204
        self.assertEqual(self.installed(), sorted([*expected, manifest]))
        for rel in expected:
            name = Path(rel).name
            self.assertEqual(
                (self.stage / rel).read_bytes(), (self.source / name).read_bytes()
            )
            self.assertEqual((self.stage / rel).stat().st_mode & 0o777, 0o644)
        self.assertEqual(result.stderr, "")
        self.assertNotIn(FAKE_SECRET, result.stdout)

    def test_default_prefix_and_flag_override(self):
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(
            all(p.startswith("usr/local/share/man/") for p in self.installed())
        )
        result = self.run_install("--prefix", "/srv/x", PREFIX="/ignored")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("srv/x/share/man/man1/parking-report.1", self.installed())
        self.assertFalse((self.stage / "ignored").exists())

    def test_relative_prefix_and_missing_page_fail_without_details(self):
        result = self.run_install(PREFIX="relative/" + FAKE_SECRET)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stderr,
            "install_manpages: failed at stage=destination error=ValueError\n",
        )
        (self.source / "parking-report-http.1").unlink()
        result = self.run_install(PREFIX="/opt/plh")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stderr,
            "install_manpages: failed at stage=copy error=FileNotFoundError\n",
        )
        self.assertEqual(self.installed(), [])


class DiagnosticsTests(unittest.TestCase):
    STAGE = re.compile(r"^(gen|install)_manpages: stage=[a-z_]+( command=[a-z0-9-]+)?$")

    def run_gen(self, *args, env):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / f"out-{FAKE_SECRET}"
            return run_script("gen_manpages", "--output", str(out), *args, env=env)

    def test_debug_is_off_by_default(self):
        result = self.run_gen(env=synthetic_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "wrote 4 man pages\n")

    def test_debug_prints_only_stage_lines(self):
        for args, env in (
            (("--debug",), synthetic_env()),
            ((), synthetic_env(PARKING_MANPAGES_DEBUG="1")),
        ):
            with self.subTest(args=args):
                result = self.run_gen(*args, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                lines = result.stderr.splitlines()
                self.assertIn("gen_manpages: stage=done", lines)
                for line in lines:
                    self.assertRegex(line, self.STAGE)
                self.assertNotIn(FAKE_SECRET, result.stdout + result.stderr)

    def test_installer_debug_prints_only_stage_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "pages"
            gen.write_pages(gen.generate(quiet_log()), source, quiet_log())
            result = run_script(
                "install_manpages",
                "--source",
                str(source),
                env=synthetic_env(
                    DESTDIR=f"{tmp}/stage-{FAKE_SECRET}",
                    PARKING_MANPAGES_DEBUG="1",
                ),
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        for line in result.stderr.splitlines():
            self.assertRegex(line, self.STAGE)
        self.assertNotIn(FAKE_SECRET, result.stdout + result.stderr)

    def test_failure_names_stage_and_class_only(self):
        err = io.StringIO()
        with (
            patch.object(gen, "CONTRACT", Path(f"/nonexistent/{FAKE_SECRET}.md")),
            contextlib.redirect_stderr(err),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            rc = gen.main(["--output", "/nonexistent"])
        self.assertEqual(rc, 1)
        self.assertEqual(
            err.getvalue(),
            "gen_manpages: failed at stage=load_contract error=FileNotFoundError\n",
        )


if __name__ == "__main__":
    unittest.main()
