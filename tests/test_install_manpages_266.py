"""Manifest write atomicity: an interrupted manifest write keeps the old one (#266).

install() writes the manifest last through the same helper as the pages
(_replace_page): the new bytes go to a temporary file created next to the
manifest in share/man (".<manifest name>.<pid>.tmp"), it is fchmod-ed and
fsync-ed, and os.replace() swaps it in. Any exception on the way, including
a BaseException such as KeyboardInterrupt, unlinks the temporary file and is
re-raised. #258 pins that the manifest is written last; these tests interrupt
the manifest write itself, at os.replace and partway through os.write, and
pin that the previous manifest is byte-identical afterwards, that no
temporary file is left behind and that the next install settles. Everything
runs offline in a temporary DESTDIR; no real man directory is touched.
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
SECRET = "s3cr3t-266-path"
DEBUG_ENV = "PARKING_MANPAGES_DEBUG"
# Captured before any patching.
REAL_OPEN = os.open
REAL_WRITE = os.write
REAL_REPLACE = os.replace
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR


class Interrupted(Exception):
    """An error in the middle of the manifest write (main() reports these)."""


class Killed(BaseException):
    """A BaseException, like KeyboardInterrupt, that main() does not catch."""


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


class ManifestWriteAtomic266(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.source = source_pages(tmp.name)
        cls.current = {p: (cls.source / p).read_bytes() for p in PAGES}
        cls.installer = load(SCRIPT, "install_manpages_266")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.top = Path(tmp.name)
        self.stage = self.top / f"stage-{SECRET}"
        self.mandir = self.stage / PREFIX.lstrip("/") / "share" / "man"
        self.man1 = self.mandir / "man1"
        self.manifest = self.mandir / MANIFEST
        self.fired = []  # what each fault injection did, in order

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

    def install(self, *patches):
        """install() in-process (no output of its own), patches applied."""
        root, below = self.installer.plan(PREFIX, str(self.stage))
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            return self.installer.install(self.source, root, below, PAGES)

    def previous_install(self):
        """A first normal install, then an owned stale page as an earlier
        release would leave it, so the next manifest differs from this one.
        Returns the manifest bytes."""
        rc, out, err = self.run_main()
        self.assertEqual((rc, err), (0, ""))
        (self.man1 / RETIRED).write_bytes(RETIRED_BYTES)
        (self.man1 / RETIRED).chmod(0o644)
        before = manifest_bytes({**self.current, RETIRED: RETIRED_BYTES})
        self.manifest.write_bytes(before)
        self.assertNotEqual(before, manifest_bytes(self.current))
        return before

    def fail_manifest_replace(self, exc):
        """os.replace raises exc when the destination is the manifest; page
        replaces go through."""

        def replace(src, dst, *args, **kwargs):
            if dst == MANIFEST:
                self.fired.append(("replace", src))
                raise exc("replace")
            return REAL_REPLACE(src, dst, *args, **kwargs)

        return mock.patch.object(self.installer.os, "replace", side_effect=replace)

    def fail_manifest_write(self, exc):
        """os.write on a descriptor opened for writing under a name that
        contains the manifest's writes the first half of the bytes, then
        raises exc. Other writes go through."""
        fds = set()

        def open_(path, flags, *args, **kwargs):
            fd = REAL_OPEN(path, flags, *args, **kwargs)
            if MANIFEST in os.fsdecode(path) and flags & WRITE_FLAGS:
                fds.add(fd)
            return fd

        def write(fd, data):
            if fd not in fds:
                return REAL_WRITE(fd, data)
            half = max(1, len(data) // 2)
            written = REAL_WRITE(fd, bytes(data[:half]))
            self.fired.append(("write", written, len(data)))
            raise exc("write")

        return (
            mock.patch.object(self.installer.os, "open", side_effect=open_),
            mock.patch.object(self.installer.os, "write", side_effect=write),
        )

    def assert_old_manifest(self, before):
        after = self.manifest.read_bytes()
        self.assertEqual(sha(after), sha(before))
        self.assertEqual(after, before)
        self.assertIn(RETIRED, self.installer.parse_manifest(after))

    def assert_no_leftovers(self):
        """No temporary file anywhere in the staging tree: only the manifest
        and man1 in share/man, only the shipped pages in man1 (cleanup ran
        before the manifest stage, so the stale page is gone)."""
        tmp = [p.name for p in self.stage.rglob("*") if p.name.endswith(".tmp")]
        self.assertEqual(tmp, [])
        self.assertEqual(
            sorted(p.name for p in self.mandir.iterdir()), sorted([MANIFEST, "man1"])
        )
        self.assertEqual(sorted(p.name for p in self.man1.iterdir()), sorted(PAGES))
        for page in PAGES:
            self.assertEqual((self.man1 / page).read_bytes(), self.current[page])

    def assert_retry_settles(self):
        """The next normal install: rc 0, a manifest listing exactly the
        shipped pages, nothing left over; a further install is a no-op."""
        for _ in range(2):
            rc, out, err = self.run_main()
            self.assertEqual((rc, err, stale_lines(out)), (0, "", []))
            for page in PAGES:
                self.assertIn(f"installed {page}\n", out)
            self.assertEqual(self.manifest.read_bytes(), manifest_bytes(self.current))
            self.assertEqual(self.manifest.stat().st_mode & 0o777, 0o644)
            self.assert_no_leftovers()

    # the write itself: complete new bytes in a temporary file next to it ---

    def test_new_manifest_is_complete_in_a_temp_file_beside_it_before_swap(self):
        before = self.previous_install()
        seen = []

        def replace(src, dst, *args, **kwargs):
            if dst == MANIFEST:
                # The moment of the swap: the old manifest is still whole and
                # the new bytes sit, complete, in a temp file in share/man.
                temps = [p for p in self.mandir.iterdir() if p.name.endswith(".tmp")]
                seen.append(
                    (
                        src,
                        [p.name for p in temps],
                        [p.read_bytes() for p in temps],
                        self.manifest.read_bytes(),
                    )
                )
            return REAL_REPLACE(src, dst, *args, **kwargs)

        self.install(
            mock.patch.object(self.installer.os, "replace", side_effect=replace)
        )
        self.assertEqual(len(seen), 1, "the manifest is not swapped in by os.replace")
        src, names, contents, old = seen[0]
        self.assertEqual(names, [src])
        self.assertIn(MANIFEST, src)
        self.assertNotEqual(src, MANIFEST)
        self.assertEqual(contents, [manifest_bytes(self.current)])
        self.assertEqual(old, before)
        self.assertEqual(self.manifest.read_bytes(), manifest_bytes(self.current))
        self.assert_no_leftovers()

    # interrupted at os.replace ---------------------------------------------

    def test_replace_interrupted_keeps_old_manifest_and_no_temp(self):
        for exc in (Interrupted, Killed):
            with self.subTest(exc=exc.__name__):
                self.setUp()
                before = self.previous_install()
                with self.assertRaises(exc):
                    self.install(self.fail_manifest_replace(exc))
                self.assertEqual([f[0] for f in self.fired], ["replace"])
                self.assert_old_manifest(before)
                self.assert_no_leftovers()
                self.assert_retry_settles()

    def test_cli_replace_interrupted_fails_at_manifest_without_paths(self):
        before = self.previous_install()
        rc, out, err = self.run_main(self.fail_manifest_replace(Interrupted))
        self.assertEqual((rc, out), (1, ""))
        self.assertEqual(
            err, "install_manpages: failed at stage=manifest error=Interrupted\n"
        )
        self.assertEqual([f[0] for f in self.fired], ["replace"])
        self.assert_old_manifest(before)
        self.assert_no_leftovers()
        self.assert_retry_settles()

    # interrupted partway through os.write -----------------------------------

    def test_write_interrupted_partway_keeps_old_manifest_and_no_temp(self):
        for exc in (Interrupted, Killed):
            with self.subTest(exc=exc.__name__):
                self.setUp()
                before = self.previous_install()
                with self.assertRaises(exc):
                    self.install(*self.fail_manifest_write(exc))
                # Some, not all, of the new manifest's bytes were written.
                self.assertEqual(len(self.fired), 1)
                _, written, total = self.fired[0]
                self.assertTrue(0 < written < total, self.fired)
                self.assert_old_manifest(before)
                self.assert_no_leftovers()
                self.assert_retry_settles()

    def test_cli_write_interrupted_fails_at_manifest_without_paths(self):
        before = self.previous_install()
        rc, out, err = self.run_main(*self.fail_manifest_write(Interrupted))
        self.assertEqual((rc, out), (1, ""))
        self.assertEqual(
            err, "install_manpages: failed at stage=manifest error=Interrupted\n"
        )
        self.assertEqual([f[0] for f in self.fired], ["write"])
        self.assert_old_manifest(before)
        self.assert_no_leftovers()
        self.assert_retry_settles()


if __name__ == "__main__":
    unittest.main()
