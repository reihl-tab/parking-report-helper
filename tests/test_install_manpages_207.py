"""Installer path safety (#207): PREFIX traversal and symlinked targets.

Temporary directories only: every victim file and every "outside" directory
lives inside the test's own temp dir. No real PREFIX, no sudo, no network.
"""

import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_manpages.py"
MAN1 = ROOT / "man" / "man1"
PAGES = ("p106-stats.1", "parking-report.1", "parking-report-106.1", "parking-report-http.1")
VICTIM_BYTES = b"synthetic victim 207, must not change\n"
VICTIM_MODE = 0o600
DESTINATION_FAILED = "install_manpages: failed at stage=destination error=ValueError\n"
UNSAFE_FAILED = "install_manpages: failed at stage=copy error=UnsafePathError\n"


def tree(top):
    """Every entry under top (relative path -> kind), symlinks not followed."""
    found = {}
    for dirpath, dirnames, filenames in os.walk(top):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            rel = str(path.relative_to(top))
            mode = path.lstat().st_mode
            found[rel] = (
                "link"
                if stat.S_ISLNK(mode)
                else "dir"
                if stat.S_ISDIR(mode)
                else "file"
            )
    return found


def source_pages(tmp):
    """The committed pages; the Docker test image has no man/, so there the
    pages are generated into tmp (the drift test proves they are equal)."""
    if MAN1.is_dir():
        return MAN1
    spec = importlib.util.spec_from_file_location(
        "gen_manpages", ROOT / "scripts" / "gen_manpages.py"
    )
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    out = Path(tmp) / "pages"
    gen.write_pages(gen.generate(gen.Log(False)), out, gen.Log(False))
    return out


class InstallerPathSafety207(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.source = source_pages(tmp.name)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.top = Path(tmp.name)
        self.fresh("case")

    def fresh(self, name):
        """A new, empty case directory, so a failed subtest cannot leak."""
        self.tmp = self.top / name
        self.stage = self.tmp / "box" / "stage"
        self.outside = self.tmp / "box" / "outside"
        self.outside.mkdir(parents=True)
        self.victim = self.outside / "victim.txt"
        self.victim.write_bytes(VICTIM_BYTES)
        self.victim.chmod(VICTIM_MODE)

    def run_install(self, prefix, destdir=None):
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PREFIX": prefix,
            "DESTDIR": str(self.stage) if destdir is None else destdir,
        }
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--source", str(self.source)],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )

    def assert_victim_unchanged(self):
        self.assertEqual(self.victim.read_bytes(), VICTIM_BYTES)
        self.assertEqual(stat.S_IMODE(self.victim.stat().st_mode), VICTIM_MODE)

    def man1(self, prefix="/opt/plh"):
        return self.stage / prefix.lstrip("/") / "share" / "man" / "man1"

    # (a) PREFIX traversal ------------------------------------------------

    def test_prefix_traversal_is_refused_and_nothing_escapes_the_stage(self):
        for n, prefix in enumerate(("/../escape", "/usr/../../escape", "/opt/plh/..")):
            with self.subTest(prefix=prefix):
                self.fresh(f"traversal-{n}")
                before = tree(self.tmp)
                result = self.run_install(prefix)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, DESTINATION_FAILED)
                self.assertEqual(result.stdout, "")
                self.assertFalse((self.tmp / "box" / "escape").exists())
                self.assertEqual(tree(self.tmp), before)

    def test_prefix_traversal_is_refused_without_destdir(self):
        prefix = f"{self.tmp}/box/stage/../escape"
        result = self.run_install(prefix, destdir="")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, DESTINATION_FAILED)
        self.assertFalse((self.tmp / "box" / "escape").exists())

    # (b) existing target page symlink ------------------------------------

    def test_existing_page_symlink_is_refused_and_victim_untouched(self):
        man1 = self.man1()
        man1.mkdir(parents=True)
        link = man1 / "parking-report-106.1"
        link.symlink_to(self.victim)
        result = self.run_install("/opt/plh")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, UNSAFE_FAILED)
        self.assert_victim_unchanged()
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(self.victim))
        # Refused before any page is written: no partial install.
        self.assertEqual(sorted(p.name for p in man1.iterdir()), [link.name])

    def test_existing_page_hard_link_is_replaced_not_written_through(self):
        man1 = self.man1()
        man1.mkdir(parents=True)
        os.link(self.victim, man1 / "parking-report.1")
        result = self.run_install("/opt/plh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_victim_unchanged()
        self.assertEqual(
            (man1 / "parking-report.1").read_bytes(),
            (self.source / "parking-report.1").read_bytes(),
        )

    # (c) symlinked parent directory --------------------------------------

    def test_symlinked_parent_directory_is_refused(self):
        for n, linked in enumerate(
            ("opt", "opt/plh/share/man", "opt/plh/share/man/man1")
        ):
            with self.subTest(linked=linked):
                self.fresh(f"parent-{n}")
                bait = self.outside / "man1"
                bait.mkdir()
                bait_page = bait / "parking-report.1"
                bait_page.write_bytes(VICTIM_BYTES)
                bait_page.chmod(VICTIM_MODE)
                link = self.stage / linked
                link.parent.mkdir(parents=True, exist_ok=True)
                target = bait if linked.endswith("man1") else self.outside
                link.symlink_to(target, target_is_directory=True)
                before = tree(self.outside)
                result = self.run_install("/opt/plh")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, UNSAFE_FAILED)
                self.assertEqual(tree(self.outside), before)
                self.assertEqual(bait_page.read_bytes(), VICTIM_BYTES)
                self.assertEqual(stat.S_IMODE(bait_page.stat().st_mode), VICTIM_MODE)
                self.assert_victim_unchanged()

    def test_symlinked_directory_below_prefix_is_refused_without_destdir(self):
        prefix = self.tmp / "prefix"
        prefix.mkdir()
        (prefix / "share").symlink_to(self.outside, target_is_directory=True)
        before = tree(self.outside)
        result = self.run_install(str(prefix), destdir="")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, UNSAFE_FAILED)
        self.assertEqual(tree(self.outside), before)

    # (d) the normal staged install still works ---------------------------

    def test_normal_staged_install_and_reinstall(self):
        man1 = self.man1()
        for run in ("fresh", "over existing pages"):
            with self.subTest(run=run):
                result = self.run_install("/opt/plh")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertEqual(sorted(p.name for p in man1.iterdir()), sorted(PAGES))
                for page in PAGES:
                    path = man1 / page
                    self.assertFalse(path.is_symlink())
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
                    self.assertEqual(
                        path.read_bytes(), (self.source / page).read_bytes()
                    )
                (man1 / PAGES[0]).write_bytes(b"stale")
                (man1 / PAGES[0]).chmod(0o600)
        self.assert_victim_unchanged()

    def test_dot_and_empty_prefix_components_stay_inside_the_stage(self):
        result = self.run_install("//opt/./plh/")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(p.name for p in self.man1().iterdir()), sorted(PAGES))


if __name__ == "__main__":
    unittest.main()
