"""Owned stale pages that cannot be read or removed are kept (#257).

Before #257, remove_stale handled only FileNotFoundError and ValueError: any
other OSError (PermissionError on an unreadable page, or from os.unlink on a
sticky or immutable entry) aborted the install at stage=cleanup before the
manifest was rewritten, so every later install failed on the same page. Now
such a page is kept ("unreadable" / "not removable") and disowned like the
other kept cases.

Everything runs offline in temporary DESTDIR stages; no real man directory
is touched. The chmod case needs a non-root user (root reads a mode-000
file), so the same paths are also covered without chmod by patching os.open
and os.unlink in the installer's namespace; those run under root too (the
Docker test stage).
"""

import contextlib
import errno
import hashlib
import importlib.util
import io
import os
import stat
import subprocess
import sys
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
SECRET = "s3cr3t-257-path"
DEBUG_ENV = "PARKING_MANPAGES_DEBUG"
REAL_UNLINK = os.unlink  # captured before any patching


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


class KeptUnreadableOrNotRemovable257(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.source = source_pages(tmp.name)
        cls.current = {p: (cls.source / p).read_bytes() for p in PAGES}
        cls.installer = load(SCRIPT, "install_manpages_257")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.top = Path(tmp.name)
        self.stage = self.top / f"stage-{SECRET}"
        self.mandir = self.stage / PREFIX.lstrip("/") / "share" / "man"
        self.man1 = self.mandir / "man1"
        self.manifest = self.mandir / MANIFEST
        self.page = self.man1 / RETIRED

    # helpers ---------------------------------------------------------------

    def check_output(self, rc, out, err):
        text = out + err
        self.assertNotIn("Traceback", text)
        self.assertNotIn(SECRET, text)
        self.assertNotIn(str(self.top), text)
        return rc, out, err

    def run_subprocess(self):
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PREFIX": PREFIX,
            "DESTDIR": str(self.stage),
        }
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--source", str(self.source)],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        return self.check_output(result.returncode, result.stdout, result.stderr)

    def run_inprocess(self):
        argv = ["--source", str(self.source), "--prefix", PREFIX]
        argv += ["--destdir", str(self.stage)]
        out, err = io.StringIO(), io.StringIO()
        env = {k: v for k, v in os.environ.items() if k != DEBUG_ENV}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            rc = self.installer.main(argv)
        return self.check_output(rc, out.getvalue(), err.getvalue())

    def owned_stale_page(self):
        """A stage as an earlier release left it: the current pages plus an
        owned page of a removed command, listed with its correct hash."""
        rc, _, err = self.run_inprocess()
        self.assertEqual(rc, 0, err)
        self.page.write_bytes(RETIRED_BYTES)
        self.manifest.write_bytes(
            manifest_bytes({**self.current, RETIRED: RETIRED_BYTES})
        )

    def assert_kept_and_disowned(self, rc, out, err, reason):
        self.assertEqual(rc, 0, err)
        self.assertEqual(err, "")
        self.assertEqual(stale_lines(out), [f"kept stale {RETIRED} ({reason})"])
        self.assertEqual(
            sorted(line for line in out.splitlines() if line.startswith("installed ")),
            sorted(f"installed {page}" for page in PAGES),
        )
        self.assertEqual(self.manifest.read_bytes(), manifest_bytes(self.current))
        for page, data in self.current.items():
            self.assertEqual((self.man1 / page).read_bytes(), data, page)

    def assert_later_install_ignores_it(self, rc, out, err):
        # Disowned: no longer in the manifest, so not retried or reported.
        self.assertEqual(rc, 0, err)
        self.assertEqual(err, "")
        self.assertEqual(stale_lines(out), [])
        self.assertEqual(self.manifest.read_bytes(), manifest_bytes(self.current))

    def guard(self, real, exc_errno, *, before=None):
        """A wrapper of os.open/os.unlink that fails only for RETIRED."""
        calls = []

        def wrapper(path, *args, **kwargs):
            if path == RETIRED and kwargs.get("dir_fd") is not None:
                calls.append(path)
                if before:
                    before(path, kwargs["dir_fd"])
                raise OSError(exc_errno, os.strerror(exc_errno))
            return real(path, *args, **kwargs)

        return wrapper, calls

    # (a) the reviewer's probe: chmod 000, three reinstalls -----------------

    @unittest.skipIf(os.geteuid() == 0, "root can read a mode-000 file")
    def test_chmod_000_owned_stale_page_is_kept_and_three_reinstalls_succeed(self):
        self.owned_stale_page()
        self.page.chmod(0o000)
        self.addCleanup(lambda: self.page.exists() and self.page.chmod(0o644))
        if os.access(self.page, os.R_OK):
            self.skipTest("this user can still read a mode-000 file")
        self.assert_kept_and_disowned(*self.run_subprocess(), "unreadable")
        for _ in range(2):
            self.assert_later_install_ignores_it(*self.run_subprocess())
        # Kept means untouched.
        self.assertEqual(stat.S_IMODE(self.page.lstat().st_mode), 0)
        self.page.chmod(0o644)
        self.assertEqual(self.page.read_bytes(), RETIRED_BYTES)

    # (b) the same logic without chmod, so it also runs as root -------------

    def test_unlink_permission_error_keeps_page_as_not_removable(self):
        self.owned_stale_page()
        wrapper, calls = self.guard(os.unlink, errno.EPERM)
        with mock.patch.object(self.installer.os, "unlink", wrapper):
            self.assert_kept_and_disowned(*self.run_inprocess(), "not removable")
            for _ in range(2):
                self.assert_later_install_ignores_it(*self.run_inprocess())
        self.assertEqual(calls, [RETIRED])  # tried once, never retried
        self.assertEqual(self.page.read_bytes(), RETIRED_BYTES)

    def test_read_permission_error_keeps_page_as_unreadable(self):
        self.owned_stale_page()
        wrapper, calls = self.guard(os.open, errno.EACCES)
        unlinked = []

        def unlink(path, *args, **kwargs):
            unlinked.append(path)
            return REAL_UNLINK(path, *args, **kwargs)

        with (
            mock.patch.object(self.installer.os, "open", wrapper),
            mock.patch.object(self.installer.os, "unlink", unlink),
        ):
            self.assert_kept_and_disowned(*self.run_inprocess(), "unreadable")
            for _ in range(2):
                self.assert_later_install_ignores_it(*self.run_inprocess())
        self.assertEqual(calls, [RETIRED])
        self.assertNotIn(RETIRED, unlinked)
        self.assertEqual(self.page.read_bytes(), RETIRED_BYTES)

    # FileNotFoundError keeps its meaning: a vanished page is simply gone ---

    def test_page_vanishing_before_read_is_neither_kept_nor_unreadable(self):
        self.owned_stale_page()

        def vanish(path, dir_fd):
            REAL_UNLINK(path, dir_fd=dir_fd)

        wrapper, calls = self.guard(os.open, errno.ENOENT, before=vanish)
        with mock.patch.object(self.installer.os, "open", wrapper):
            self.assert_later_install_ignores_it(*self.run_inprocess())
        self.assertEqual(calls, [RETIRED])
        self.assertFalse(self.page.exists())

    def test_page_vanishing_before_unlink_is_neither_kept_nor_removed(self):
        self.owned_stale_page()

        def vanish(path, dir_fd):
            REAL_UNLINK(path, dir_fd=dir_fd)

        wrapper, calls = self.guard(os.unlink, errno.ENOENT, before=vanish)
        with mock.patch.object(self.installer.os, "unlink", wrapper):
            self.assert_later_install_ignores_it(*self.run_inprocess())
        self.assertEqual(calls, [RETIRED])
        self.assertFalse(self.page.exists())

    # the existing behaviour is unchanged -----------------------------------

    def test_removable_owned_stale_page_is_still_removed(self):
        self.owned_stale_page()
        rc, out, err = self.run_inprocess()
        self.assertEqual(rc, 0, err)
        self.assertEqual(stale_lines(out), [f"removed stale {RETIRED}"])
        self.assertFalse(self.page.exists())
        self.assertEqual(self.manifest.read_bytes(), manifest_bytes(self.current))


if __name__ == "__main__":
    unittest.main()
