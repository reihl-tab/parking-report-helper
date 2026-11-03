"""Manifest written last: an interrupted install is retried (#258).

install_manpages.py's docstring and docs/CLI-CONTRACT.md say "the manifest is
written last, so an interrupted cleanup is retried next time". These tests
pin that contract by running install() in-process with its real `at` stage
callback (install() reports "cleanup" before remove_stale and "manifest"
before the manifest write) and raising there, or inside cleanup itself.
The previous manifest must survive an interruption byte-identical, so the
next normal install still owns, and removes, the stale page. Everything runs
offline in a temporary DESTDIR; no real man directory is touched.
"""

import contextlib
import hashlib
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_manpages.py"
MAN1 = ROOT / "man" / "man1"
PAGES = (
    "p106-stats.1",
    "parking-report.1",
    "parking-report-106.1",
    "parking-report-http.1",
)
PREFIX = "/opt/plh"
MANIFEST = ".parking-report-helper-man1.manifest"
HEADER = b"# parking-report-helper man1 pages installed by install_manpages.py\n"
RETIRED = "parking-report-retired.1"
RETIRED_BYTES = b".TH PARKING-REPORT-RETIRED 1\n.SH NAME\nretired \\- gone\n"
OTHER = "parking-report-zzz-retired.1"  # sorts after RETIRED in cleanup
OTHER_BYTES = b".TH PARKING-REPORT-ZZZ-RETIRED 1\n.SH NAME\nzzz \\- gone\n"
SECRET = "s3cr3t-258-path"
DEBUG_ENV = "PARKING_MANPAGES_DEBUG"
REAL_UNLINK = os.unlink  # captured before any patching


class Interrupted(Exception):
    """Stands in for a crash or a kill between two install stages."""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def manifest_bytes(entries):
    """entries: page name -> bytes it was installed with."""
    lines = [f"{sha(data)}  {name}\n" for name, data in sorted(entries.items())]
    return HEADER + "".join(lines).encode("ascii")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_pages(tmp):
    """The committed pages; without man/ they are generated into tmp
    (test_manpages_188 proves they are equal)."""
    if MAN1.is_dir():
        return MAN1
    gen = load(ROOT / "scripts" / "gen_manpages.py", "gen_manpages")
    out = Path(tmp) / "pages"
    gen.write_pages(gen.generate(gen.Log(False)), out, gen.Log(False))
    return out


def stale_lines(output):
    return [line for line in output.splitlines() if "stale" in line]


def raise_at(target):
    def at(stage):
        if stage == target:
            raise Interrupted(stage)

    return at


class ManifestWrittenLast258(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.source = source_pages(tmp.name)
        cls.current = {p: (cls.source / p).read_bytes() for p in PAGES}
        cls.installer = load(SCRIPT, "install_manpages_258")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.top = Path(tmp.name)
        self.stage = self.top / f"stage-{SECRET}"
        self.mandir = self.stage / PREFIX.lstrip("/") / "share" / "man"
        self.man1 = self.mandir / "man1"
        self.manifest = self.mandir / MANIFEST

    # helpers ---------------------------------------------------------------

    def run_main(self, *patches):
        """main() in-process, debug off, with stdout/stderr captured."""
        argv = ["--source", str(self.source), "--prefix", PREFIX]
        argv += ["--destdir", str(self.stage)]
        out, err = io.StringIO(), io.StringIO()
        env = {k: v for k, v in os.environ.items() if k != DEBUG_ENV}
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, env, clear=True))
            for patch in patches:
                stack.enter_context(patch)
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            rc = self.installer.main(argv)
        out, err = out.getvalue(), err.getvalue()
        text = out + err
        self.assertNotIn("Traceback", text)
        self.assertNotIn(SECRET, text)
        self.assertNotIn(str(self.top), text)
        return rc, out, err

    def install(self, at):
        """install() in-process with the given stage callback."""
        root, below = self.installer.plan(PREFIX, str(self.stage))
        return self.installer.install(self.source, root, below, PAGES, at)

    def previous_install_with(self, extra):
        """A first normal install, then owned stale pages as an earlier
        release would leave them: present, and in the manifest with the hash
        they were installed with. Returns the manifest bytes."""
        rc, out, err = self.run_main()
        self.assertEqual((rc, err), (0, ""))
        for name, data in extra.items():
            (self.man1 / name).write_bytes(data)
            (self.man1 / name).chmod(0o644)
        manifest = manifest_bytes({**self.current, **extra})
        self.manifest.write_bytes(manifest)
        self.assertEqual(
            self.installer.parse_manifest(manifest),
            {name: sha(data) for name, data in {**self.current, **extra}.items()},
        )
        return manifest

    def assert_manifest_unchanged(self, before):
        after = self.manifest.read_bytes()
        self.assertEqual(sha(after), sha(before))
        self.assertEqual(after, before)

    def assert_current_pages(self):
        for page in PAGES:
            self.assertEqual((self.man1 / page).read_bytes(), self.current[page])

    def assert_no_leftovers(self, *stale):
        """Only the manifest and man1 in share/man, only pages in man1: an
        interruption leaves no temporary file behind."""
        self.assertEqual(
            sorted(p.name for p in self.mandir.iterdir()), sorted([MANIFEST, "man1"])
        )
        self.assertEqual(
            sorted(p.name for p in self.man1.iterdir()), sorted([*PAGES, *stale])
        )

    def assert_retry_is_clean(self, expected_stale_lines):
        """The next normal install: rc 0, only the expected stale lines, the
        stale pages gone and a manifest listing exactly the shipped pages."""
        rc, out, err = self.run_main()
        self.assertEqual(rc, 0, err)
        self.assertEqual(err, "")
        for page in PAGES:
            self.assertIn(f"installed {page}\n", out)
        self.assertEqual(stale_lines(out), expected_stale_lines)
        self.assertEqual(self.manifest.read_bytes(), manifest_bytes(self.current))
        self.assert_current_pages()
        self.assert_no_leftovers()
        # And it stays settled.
        rc, out, err = self.run_main()
        self.assertEqual((rc, err, stale_lines(out)), (0, "", []))
        self.assertEqual(self.manifest.read_bytes(), manifest_bytes(self.current))

    # the stage order the contract rests on ---------------------------------

    def test_stage_order_pages_then_cleanup_then_manifest(self):
        before = self.previous_install_with({RETIRED: RETIRED_BYTES})
        seen = []

        def at(stage):
            # What is on disk when each stage starts.
            seen.append(
                (
                    stage,
                    (self.man1 / RETIRED).exists(),
                    self.manifest.read_bytes() == before,
                )
            )

        result = self.install(at)
        self.assertEqual(
            seen,
            [
                ("cleanup", True, True),  # pages copied, nothing removed yet
                ("manifest", False, True),  # stale page removed, old manifest
            ],
        )
        self.assertEqual(result.removed, [RETIRED])
        self.assertEqual(self.manifest.read_bytes(), manifest_bytes(self.current))

    # interrupted at "manifest": cleanup done, old manifest kept ------------

    def test_interrupt_at_manifest_keeps_old_manifest_and_retry_settles(self):
        before = self.previous_install_with({RETIRED: RETIRED_BYTES})
        with self.assertRaises(Interrupted):
            self.install(raise_at("manifest"))
        # The previous manifest is byte-identical and still owns the page.
        self.assert_manifest_unchanged(before)
        self.assertIn(RETIRED, self.installer.parse_manifest(before))
        # Cleanup runs before the manifest stage, so the page is already gone;
        # the shipped pages are the current ones and nothing else is left.
        self.assertFalse((self.man1 / RETIRED).exists())
        self.assert_current_pages()
        self.assert_no_leftovers()
        # The retry finds the owned page already gone, says nothing about it
        # and writes a manifest without it.
        self.assert_retry_is_clean([])

    def test_cli_reports_failure_at_stage_manifest_without_paths(self):
        before = self.previous_install_with({RETIRED: RETIRED_BYTES})
        fail = mock.patch.object(
            self.installer, "format_manifest", side_effect=Interrupted("x")
        )
        rc, out, err = self.run_main(fail)
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertEqual(
            err, "install_manpages: failed at stage=manifest error=Interrupted\n"
        )
        self.assert_manifest_unchanged(before)
        self.assertFalse((self.man1 / RETIRED).exists())
        self.assert_no_leftovers()
        self.assert_retry_is_clean([])

    # interrupted before cleanup: the next install removes the page ---------

    def test_interrupt_at_cleanup_then_next_install_removes_owned_page(self):
        before = self.previous_install_with({RETIRED: RETIRED_BYTES})
        with self.assertRaises(Interrupted):
            self.install(raise_at("cleanup"))
        self.assert_manifest_unchanged(before)
        self.assertEqual((self.man1 / RETIRED).read_bytes(), RETIRED_BYTES)
        self.assert_current_pages()
        self.assert_no_leftovers(RETIRED)
        self.assert_retry_is_clean([f"removed stale {RETIRED}"])

    # interrupted inside cleanup: the rest is removed next time --------------

    def test_interrupt_inside_cleanup_then_next_install_removes_the_rest(self):
        before = self.previous_install_with(
            {RETIRED: RETIRED_BYTES, OTHER: OTHER_BYTES}
        )

        def unlink(path, *args, **kwargs):
            if path == OTHER:
                raise Interrupted("unlink")
            return REAL_UNLINK(path, *args, **kwargs)

        with (
            mock.patch.object(self.installer.os, "unlink", side_effect=unlink),
            self.assertRaises(Interrupted),
        ):
            self.install(lambda stage: None)
        # RETIRED went first (sorted order), OTHER was not reached.
        self.assert_manifest_unchanged(before)
        self.assertFalse((self.man1 / RETIRED).exists())
        self.assertEqual((self.man1 / OTHER).read_bytes(), OTHER_BYTES)
        self.assert_no_leftovers(OTHER)
        self.assert_retry_is_clean([f"removed stale {OTHER}"])


if __name__ == "__main__":
    unittest.main()
