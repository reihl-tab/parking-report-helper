"""Stale man pages of removed commands: ownership-aware cleanup (#204).

The installer records the pages it wrote, with their SHA-256, in
share/man/.parking-report-helper-man1.manifest and on a later install
removes only pages listed there that are no longer shipped and are still the
regular file it wrote. Everything runs offline in temporary DESTDIR stages;
no real man directory is touched. A "removed command" is simulated with a
synthetic page added to the previous manifest; committed pages are never
edited.
"""

import hashlib
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
SECRET = "s3cr3t-204-path"
IGNORED = (
    "install_manpages: previous manifest ignored error=ManifestError; "
    "no stale page removed\n"
)
UNSAFE_FAILED = "install_manpages: failed at stage=copy error=UnsafePathError\n"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def manifest_bytes(entries):
    """entries: page name -> bytes it was installed with."""
    lines = [f"{sha(data)}  {name}\n" for name, data in sorted(entries.items())]
    return HEADER + "".join(lines).encode("ascii")


def source_pages(tmp):
    """The committed pages; in a checkout without man/ they are generated
    into tmp (test_manpages_188 proves they are equal)."""
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


def snapshot(top):
    """Relative path -> (kind, bytes or link target, mode), links not followed."""
    found = {}
    for dirpath, dirnames, filenames in os.walk(top):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            st = path.lstat()
            if stat.S_ISLNK(st.st_mode):
                value = ("link", os.readlink(path), None)
            elif stat.S_ISDIR(st.st_mode):
                value = ("dir", None, None)
            else:
                value = ("file", path.read_bytes(), stat.S_IMODE(st.st_mode))
            found[str(path.relative_to(top))] = value
    return found


class StaleCleanup204(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.source = source_pages(tmp.name)
        cls.current = {p: (cls.source / p).read_bytes() for p in PAGES}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.top = Path(tmp.name)
        self.fresh("case")

    def fresh(self, name):
        """A new stage per (sub)case, so one case cannot leak into the next."""
        self.stage = self.top / name / f"stage-{SECRET}"
        self.outside = self.top / name / "outside"
        self.outside.mkdir(parents=True)
        self.victim = self.outside / RETIRED
        self.victim.write_bytes(RETIRED_BYTES)
        self.victim.chmod(0o600)
        self.mandir = self.stage / PREFIX.lstrip("/") / "share" / "man"
        self.man1 = self.mandir / "man1"
        self.manifest = self.mandir / MANIFEST

    def run_install(self, *args):
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PREFIX": PREFIX,
            "DESTDIR": str(self.stage),
        }
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--source", str(self.source), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        return result

    def install_ok(self):
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def previous_install_with(self, extra, manifest=None):
        """A stage as an earlier release would leave it: current pages plus
        the extra pages, and a manifest listing them all (or `manifest`)."""
        self.install_ok()
        for name, data in extra.items():
            (self.man1 / name).write_bytes(data)
            (self.man1 / name).chmod(0o644)
        if manifest is None:
            manifest = manifest_bytes({**self.current, **extra})
        self.manifest.write_bytes(manifest)

    def assert_current_pages(self):
        for page in PAGES:
            path = self.man1 / page
            self.assertFalse(path.is_symlink())
            self.assertEqual(path.read_bytes(), self.current[page])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)

    def assert_victim_unchanged(self):
        self.assertEqual(self.victim.read_bytes(), RETIRED_BYTES)
        self.assertEqual(stat.S_IMODE(self.victim.stat().st_mode), 0o600)

    # (1) the first install records exactly what it installed --------------

    def test_first_install_writes_manifest_of_exactly_the_installed_pages(self):
        result = self.install_ok()
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.manifest.read_bytes(), manifest_bytes(self.current))
        self.assertEqual(stat.S_IMODE(self.manifest.stat().st_mode), 0o644)
        # The manifest sits next to man1, so man1 holds only pages.
        self.assertEqual(sorted(p.name for p in self.man1.iterdir()), sorted(PAGES))
        self.assertEqual(
            sorted(p.name for p in self.mandir.iterdir()), sorted([MANIFEST, "man1"])
        )
        self.assert_current_pages()

    # (2) an owned page of a removed command is removed --------------------

    def test_owned_page_of_removed_command_is_removed(self):
        self.previous_install_with({RETIRED: RETIRED_BYTES})
        result = self.install_ok()
        self.assertEqual(result.stderr, "")
        self.assertIn(f"removed stale {RETIRED}\n", result.stdout)
        self.assertFalse((self.man1 / RETIRED).exists())
        self.assertEqual(sorted(p.name for p in self.man1.iterdir()), sorted(PAGES))
        self.assert_current_pages()
        # The new manifest no longer claims the removed page.
        self.assertEqual(self.manifest.read_bytes(), manifest_bytes(self.current))

    # (3) unrelated pages are never touched --------------------------------

    def test_unrelated_pages_survive_beside_an_owned_stale_page(self):
        man1_unrelated = {
            "p106-stats-extra.1": b"similar name, not ours\n",
            "parking-report-retired.1.bak": b"backup, not ours\n",
            "parking-report-old.1": b"same prefix, never in a manifest\n",
            "other-tool.1": b"another package\n",
        }
        self.previous_install_with({"parking-report-gone.1": RETIRED_BYTES})
        for name, data in man1_unrelated.items():
            (self.man1 / name).write_bytes(data)
        for section in ("man5", "man8"):
            (self.mandir / section).mkdir()
            (self.mandir / section / "parking-report-gone.5").write_bytes(b"x\n")
        before = snapshot(self.stage)
        result = self.install_ok()
        after = snapshot(self.stage)
        gone = "opt/plh/share/man/man1/parking-report-gone.1"
        self.assertEqual(set(before) - set(after), {gone})
        self.assertEqual(set(after) - set(before), set())
        for rel, value in before.items():
            if rel != gone and not rel.endswith(MANIFEST):
                self.assertEqual(after[rel], value, rel)
        self.assertEqual(
            [line for line in result.stdout.splitlines() if "stale" in line],
            ["removed stale parking-report-gone.1"],
        )

    def test_pages_from_an_install_without_manifest_are_not_owned(self):
        # An install made before #204 left no manifest: it owns nothing.
        self.install_ok()
        self.manifest.unlink()
        (self.man1 / RETIRED).write_bytes(RETIRED_BYTES)
        result = self.install_ok()
        self.assertEqual(result.stderr, "")
        self.assertEqual((self.man1 / RETIRED).read_bytes(), RETIRED_BYTES)
        self.assertEqual(self.manifest.read_bytes(), manifest_bytes(self.current))

    # (4) missing or corrupt manifest: nothing removed, no traceback --------

    def test_corrupt_manifest_removes_nothing_and_says_so(self):
        good = manifest_bytes({**self.current, RETIRED: RETIRED_BYTES})
        line = f"{sha(RETIRED_BYTES)}  {RETIRED}\n".encode()
        cases = {
            "garbage": b"\x00\xff not a manifest\n",
            "empty": b"",
            "non-ascii": good + "\u00e9\n".encode(),
            "wrong header": b"# some other tool\n" + line,
            "no header": line,
            "truncated": good[:-1],
            "duplicate entry": good + line,
            "bad hash": HEADER + f"{'0' * 63}  {RETIRED}\n".encode(),
            "uppercase hash": HEADER + line.upper(),
            "tab separator": HEADER + line.replace(b"  ", b"\t"),
            "trailing space": HEADER + line[:-1] + b" \n",
            "oversize": good + b"#" * (65 * 1024) + b"\n",
        }
        for name, data in cases.items():
            with self.subTest(manifest=name):
                self.fresh(name)
                self.previous_install_with({RETIRED: RETIRED_BYTES}, data)
                result = self.install_ok()
                self.assertEqual(result.stderr, IGNORED)
                self.assertNotIn("removed", result.stdout)
                self.assertEqual((self.man1 / RETIRED).read_bytes(), RETIRED_BYTES)
                self.assert_current_pages()
                # A fresh, valid manifest replaces the unusable one.
                self.assertEqual(
                    self.manifest.read_bytes(), manifest_bytes(self.current)
                )

    def test_symlinked_or_non_file_manifest_is_refused(self):
        for kind in ("symlink", "directory"):
            with self.subTest(kind=kind):
                self.fresh(kind)
                self.install_ok()
                self.manifest.unlink()
                if kind == "symlink":
                    self.manifest.symlink_to(self.victim)
                else:
                    self.manifest.mkdir()
                before = snapshot(self.stage)
                result = self.run_install()
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, UNSAFE_FAILED)
                self.assertEqual(snapshot(self.stage), before)
                self.assert_victim_unchanged()

    # (5) entries that would leave man1 are refused ------------------------

    def test_path_escape_entries_are_refused(self):
        makers = (
            lambda: os.path.relpath(self.victim, self.man1),
            lambda: "../" + RETIRED,
            lambda: str(self.victim),
            lambda: "/" + RETIRED,
            lambda: "man5/" + RETIRED,
            lambda: "./" + RETIRED,
            lambda: "." + RETIRED,
            lambda: RETIRED + "/",
            lambda: "..",
            lambda: "",
        )
        for index, make in enumerate(makers):
            self.fresh(f"escape-{index}")
            entry = make()
            with self.subTest(entry=entry):
                # Every entry carries the hash of bytes that do exist where
                # it points, so only the name check stands in the way.
                manifest = manifest_bytes(self.current) + (
                    f"{sha(RETIRED_BYTES)}  {entry}\n".encode()
                )
                self.previous_install_with({}, manifest)
                (self.mandir / RETIRED).write_bytes(RETIRED_BYTES)
                (self.man1 / RETIRED).write_bytes(RETIRED_BYTES)
                before = snapshot(self.stage)
                result = self.install_ok()
                self.assertEqual(result.stderr, IGNORED)
                self.assertNotIn("removed", result.stdout)
                self.assert_victim_unchanged()
                after = snapshot(self.stage)
                for rel in (f"{RETIRED}", f"man1/{RETIRED}"):
                    rel = f"opt/plh/share/man/{rel}"
                    self.assertEqual(after[rel], before[rel], rel)
                self.assertEqual(set(after), set(before))

    def test_symlinked_owned_page_is_kept_and_target_untouched(self):
        self.previous_install_with({})
        (self.man1 / RETIRED).symlink_to(self.victim)
        self.manifest.write_bytes(
            manifest_bytes({**self.current, RETIRED: RETIRED_BYTES})
        )
        result = self.install_ok()
        self.assertEqual(result.stderr, "")
        self.assertIn(f"kept stale {RETIRED} (not a regular file)\n", result.stdout)
        self.assertTrue((self.man1 / RETIRED).is_symlink())
        self.assert_victim_unchanged()
        # Kept pages are disowned: the new manifest lists only current pages.
        self.assertEqual(self.manifest.read_bytes(), manifest_bytes(self.current))

    def test_modified_owned_page_is_kept(self):
        self.previous_install_with({RETIRED: RETIRED_BYTES})
        (self.man1 / RETIRED).write_bytes(RETIRED_BYTES + b'.\\" local edit\n')
        result = self.install_ok()
        self.assertIn(f"kept stale {RETIRED} (changed since install)\n", result.stdout)
        self.assertTrue((self.man1 / RETIRED).is_file())
        self.assertEqual(self.manifest.read_bytes(), manifest_bytes(self.current))

    # (6) no dry-run mode exists, so there is nothing to test for it -------

    # (7) re-installing is idempotent ---------------------------------------

    def test_reinstall_is_idempotent(self):
        first = self.install_ok()
        before = snapshot(self.stage)
        for run in range(2):
            with self.subTest(run=run):
                again = self.install_ok()
                self.assertEqual(again.stdout, first.stdout)
                self.assertEqual(again.stderr, "")
                self.assertEqual(snapshot(self.stage), before)
        self.assertEqual(
            sorted(first.stdout.splitlines()), sorted(f"installed {p}" for p in PAGES)
        )


if __name__ == "__main__":
    unittest.main()
