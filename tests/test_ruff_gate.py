"""Contract for the ruff CI job and config (#64); runs the real step script offline."""

import os
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
STEP = "ruff check and format check (blocking when repo variable RUFF_GATE=on)"


def job_block(text, job):
    lines = text.splitlines()
    start = lines.index(f"  {job}:")
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if lines[i].startswith("  ") and not lines[i].startswith("   ")
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def step_script(text):
    """Return the `run: |` body of the gate step, dedented."""
    lines = job_block(text, "ruff").splitlines()
    i = next(n for n, line in enumerate(lines) if line.strip() == f"- name: {STEP}")
    i = next(n for n in range(i, len(lines)) if lines[n].strip() == "run: |")
    body = []
    for line in lines[i + 1 :]:
        if line.strip() and not line.startswith(" " * 10):
            break
        body.append(line[10:])
    return "\n".join(body) + "\n"


@unittest.skipUnless(WORKFLOW.exists(), "workflows are excluded from the Docker image")
class RuffWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.job = job_block(self.text, "ruff")

    def test_job_pins_ruff_and_runs_both_checks(self):
        self.assertIn('pip install "ruff==', self.job)
        self.assertIn("ruff check --output-format=github .", self.job)
        self.assertIn("ruff format --check .", self.job)
        self.assertIn("RUFF_GATE: ${{ vars.RUFF_GATE }}", self.job)
        self.assertNotIn("continue-on-error", self.job)

    def run_step(self, check_rc, format_rc, gate):
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash not available")
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "ruff"
            fake.write_text(
                "#!/bin/sh\n"
                f'[ "$1" = check ] && exit {check_rc}\n'
                f'[ "$1" = format ] && exit {format_rc}\n'
                "exit 99\n"
            )
            fake.chmod(0o755)
            env = {"PATH": tmp + os.pathsep + os.environ.get("PATH", "")}
            if gate is not None:
                env["RUFF_GATE"] = gate
            return subprocess.run(
                [bash, "-e", "-c", step_script(self.text)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

    def test_clean_passes_with_and_without_gate(self):
        for gate in (None, "on"):
            with self.subTest(gate=gate):
                self.assertEqual(self.run_step(0, 0, gate).returncode, 0)

    def test_findings_warn_only_until_gate_on(self):
        for check_rc, format_rc in ((1, 0), (0, 1), (1, 1)):
            with self.subTest(check=check_rc, fmt=format_rc):
                off = self.run_step(check_rc, format_rc, None)
                self.assertEqual(off.returncode, 0)
                self.assertIn("::warning::ruff reported findings", off.stdout)
                self.assertEqual(
                    self.run_step(check_rc, format_rc, "off").returncode, 0
                )
                self.assertEqual(self.run_step(check_rc, format_rc, "on").returncode, 1)


@unittest.skipUnless(
    (ROOT / "pyproject.toml").exists(), "pyproject.toml is not in the Docker test stage"
)
class RuffConfigTests(unittest.TestCase):
    def test_rule_set_and_target_are_explicit(self):
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        ruff = config["tool"]["ruff"]
        self.assertEqual(ruff["target-version"], "py311")
        self.assertEqual(ruff["line-length"], 88)
        self.assertEqual(ruff["lint"]["select"], ["E4", "E7", "E9", "F", "I"])


if __name__ == "__main__":
    unittest.main()
