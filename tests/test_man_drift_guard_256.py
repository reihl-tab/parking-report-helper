"""The man drift guard (#256): without man/ the drift tests skip, unless
PARKING_REQUIRE_MAN_DRIFT=1 (set only in the Docker test stage) turns the skip
into a failure; with man/ present they run either way. Offline and temp-dir
only: the real man/ tree is never moved, edited or deleted; a missing man/ is
a temp path that does not exist.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import man_drift_guard as guard
import test_manpages_188
import test_manpages_date_206

ROOT = Path(__file__).resolve().parents[1]
FLAG = "PARKING_REQUIRE_MAN_DRIFT"
DRIFT_CLASSES = (
    test_manpages_188.DriftTests,
    test_manpages_date_206.CommittedPages206,
)


def probe_case(man1, env):
    """A one-test case guarded like the drift classes; records whether it ran."""

    class Probe(unittest.TestCase):
        ran = False

        def setUp(self):
            guard.require_man_pages(self, man1, env)

        def test_body(self):
            type(self).ran = True

    return Probe


def run_case(case):
    result = unittest.TestResult()
    unittest.defaultTestLoader.loadTestsFromTestCase(case).run(result)
    return result


def without(env, name):
    return {k: v for k, v in env.items() if k != name}


class Flag256(unittest.TestCase):
    def test_name_is_the_documented_one(self):
        self.assertEqual(guard.REQUIRE_ENV, FLAG)

    def test_only_exactly_one_turns_it_on(self):
        self.assertFalse(guard.drift_required({}))
        for value in ("", "0", "true", "yes", " 1", "1 ", "2"):
            with self.subTest(value=value):
                self.assertFalse(guard.drift_required({FLAG: value}))
        self.assertTrue(guard.drift_required({FLAG: "1"}))

    def test_defaults_to_the_process_environment(self):
        with patch.dict(os.environ, {FLAG: "1"}):
            self.assertTrue(guard.drift_required())
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(FLAG, None)
            self.assertFalse(guard.drift_required())


class Guard256(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.present = Path(tmp.name) / "man" / "man1"
        self.present.mkdir(parents=True)
        self.missing = Path(tmp.name) / "absent" / "man" / "man1"
        self.tmp = tmp.name

    def test_missing_without_flag_skips(self):
        for env in ({}, {FLAG: "0"}):
            with self.subTest(env=env):
                case = probe_case(self.missing, env)
                result = run_case(case)
                self.assertEqual(result.testsRun, 1)
                self.assertEqual(result.failures + result.errors, [])
                self.assertEqual(
                    [reason for _, reason in result.skipped], [guard.SKIP_REASON]
                )
                self.assertFalse(case.ran)

    def test_missing_with_flag_fails_with_a_pathless_message(self):
        case = probe_case(self.missing, {FLAG: "1"})
        result = run_case(case)
        self.assertEqual(result.skipped, [])
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.failures), 1)
        message = result.failures[0][1]
        self.assertIn(guard.FAIL_MESSAGE, message)
        self.assertIn(FLAG + "=1", message)
        self.assertNotIn(self.tmp, message)
        self.assertNotIn(str(ROOT), guard.FAIL_MESSAGE)
        self.assertFalse(case.ran)

    def test_present_runs_with_or_without_flag(self):
        for env in ({}, {FLAG: "1"}):
            with self.subTest(env=env):
                case = probe_case(self.present, env)
                result = run_case(case)
                self.assertEqual(result.testsRun, 1)
                self.assertEqual(result.skipped, [])
                self.assertEqual(result.failures + result.errors, [])
                self.assertTrue(case.ran)


class DriftClassesUseTheGuard256(unittest.TestCase):
    """The real drift classes, pointed at a missing man/ by patching their
    module's MAN1; the committed tree is untouched."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.missing = Path(tmp.name) / "man" / "man1"

    def run_drift_classes(self, flag):
        env = without(os.environ, FLAG)
        if flag is not None:
            env[FLAG] = flag
        results = []
        with patch.dict(os.environ, env, clear=True):
            for cls in DRIFT_CLASSES:
                module = sys.modules[cls.__module__]
                with patch.object(module, "MAN1", self.missing):
                    results.append(run_case(cls))
        return results

    def test_without_flag_every_drift_test_skips(self):
        for result in self.run_drift_classes(None):
            self.assertEqual(result.testsRun, 2)
            self.assertEqual(result.failures + result.errors, [])
            self.assertEqual(
                [reason for _, reason in result.skipped], [guard.SKIP_REASON] * 2
            )

    def test_with_flag_every_drift_test_fails(self):
        for result in self.run_drift_classes("1"):
            self.assertEqual(result.testsRun, 2)
            self.assertEqual(result.skipped, [])
            self.assertEqual(result.errors, [])
            self.assertEqual(len(result.failures), 2)
            for _, message in result.failures:
                self.assertIn(guard.FAIL_MESSAGE, message)


class CheckoutWithoutMan256(unittest.TestCase):
    """End to end in a separate process: a copy of the drift test files and
    the generator in a temp root that has no man/, as a build that dropped
    the COPY line would see it."""

    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.root = Path(tmp.name) / "checkout"
        (cls.root / "tests").mkdir(parents=True)
        (cls.root / "scripts").mkdir()
        for name in (
            "man_drift_guard.py",
            "test_manpages_188.py",
            "test_manpages_date_206.py",
        ):
            shutil.copy2(ROOT / "tests" / name, cls.root / "tests" / name)
        shutil.copy2(
            ROOT / "scripts" / "gen_manpages.py",
            cls.root / "scripts" / "gen_manpages.py",
        )

    def run_drift(self, flag):
        env = without(os.environ, FLAG)
        if flag is not None:
            env[FLAG] = flag
        paths = [str(self.root / "tests")]
        paths += [
            os.path.abspath(p) for p in env.get("PYTHONPATH", "").split(os.pathsep) if p
        ]
        env["PYTHONPATH"] = os.pathsep.join(paths)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "unittest",
                "test_manpages_188.DriftTests",
                "test_manpages_date_206.CommittedPages206",
            ],
            cwd=self.root / "tests",
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
            check=False,
        )

    def test_without_flag_the_four_skip_and_the_run_passes(self):
        result = self.run_drift(None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ran 4 tests", result.stderr)
        self.assertIn("OK (skipped=4)", result.stderr)

    def test_with_flag_the_four_fail_and_the_run_fails(self):
        result = self.run_drift("1")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Ran 4 tests", result.stderr)
        self.assertIn("FAILED (failures=4)", result.stderr)
        self.assertEqual(result.stderr.count(guard.FAIL_MESSAGE), 4)
        self.assertNotIn("skipped", result.stderr)


if __name__ == "__main__":
    unittest.main()
