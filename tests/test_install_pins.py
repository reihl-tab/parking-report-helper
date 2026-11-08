"""Offline guard for hash-locked Python installs (#177).

Synthetic command lines and temporary copies of the locks only: nothing here
installs a package, reaches a network or reads a private file. The repository
scan runs where the Dockerfiles and workflows exist (a source checkout); the
Docker test stage has neither, so it runs the synthetic cases only.
"""

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_install_pins.py"
spec = importlib.util.spec_from_file_location("check_install_pins", SCRIPT)
c = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = c  # dataclasses look up annotations here
spec.loader.exec_module(c)

SOURCE_CHECKOUT = all(
    (ROOT / name).is_file()
    for name in ("Dockerfile", "pyproject.toml", ".github/workflows/tests.yml")
)
HAS_LOCKS = (ROOT / "requirements" / "lock-evidence.json").is_file() and (
    ROOT / "pyproject.toml"
).is_file()
GOOD_BUILD = (
    "RUN pip install --no-cache-dir --require-hashes -r requirements/build.txt\n"
)


def rules(text, path="Dockerfile"):
    return [f.rule for f in c.scan_text(path, text)]


def run(text):
    """One Dockerfile RUN line (plus FROM), so code-mode rules apply."""
    return rules("FROM python:3.13-slim AS base\nRUN " + text + "\n")


class PipInvocationTests(unittest.TestCase):
    def assertFlagged(self, text, needle):
        found = run(text)
        self.assertTrue(any(needle in r for r in found), f"{text!r}: {found}")

    def test_pip_and_python_m_pip_named_installs(self):
        for text in (
            "pip install requests",
            "pip3 install requests",
            "pip3.13 install requests",
            "python -m pip install requests",
            "python3.12 -m pip install requests",
            "/usr/local/bin/python3 -m pip install requests",
            "python -mpip install requests",
            "python -Im pip install requests",
            "env PIP_NO_CACHE_DIR=1 pip install requests",
        ):
            with self.subTest(text=text):
                self.assertFlagged(text, "unpinned install without a hash lock")

    def test_pip_global_options_before_the_verb(self):
        for text in (
            "python -m pip --disable-pip-version-check install requests",
            "pip -q install requests",
            "pip -qq --isolated install requests",
            "python -m pip --python /usr/bin/python3 install requests",
            "pip --timeout=30 --retries 3 install requests",
            "pip --log /tmp/pip.log -v download requests",
            "pip --no-input wheel requests",
        ):
            with self.subTest(text=text):
                self.assertFlagged(text, "install without a hash lock")

    def test_pipx_cannot_verify_hashes(self):
        for text in (
            "pipx install ruff",
            "pipx run ruff check .",
            "pipx inject ruff something",
            "python -m pipx install ruff",
            "pipx --verbose install ruff==0.16.9",
        ):
            with self.subTest(text=text):
                self.assertFlagged(text, "pipx")
        self.assertEqual(run("pipx list"), [])

    def test_requirements_install_needs_require_hashes(self):
        for text in (
            "pip install -r requirements/api.txt",
            "pip install -rrequirements/api.txt",
            "pip install --requirement=requirements/api.txt",
            "python -m pip install --requirement requirements/ci.txt",
            "uv pip install -r requirements/ci.txt",
        ):
            with self.subTest(text=text):
                self.assertFlagged(text, "without --require-hashes")
        self.assertEqual(
            run("pip install --require-hashes -r requirements/api.txt"), []
        )
        self.assertFlagged(
            "pip install --require-hashes -r requirements-extra.txt",
            "not a maintained lock",
        )

    def test_tag_only_and_unpinned_installs(self):
        self.assertFlagged("pip install ruff", "unpinned install")
        self.assertFlagged('pip install "mypy>=1.13,<2"', "unpinned install")
        self.assertFlagged(
            'pip install "ruff==0.16.9"', "pinned install without a hash"
        )
        self.assertFlagged(
            "pip install git+https://example.invalid/tool.git@v1.2.3", "tag-only"
        )
        self.assertFlagged(
            "pip install https://example.invalid/tool-1.0.tar.gz", "tag-only"
        )

    def test_core_wheels_install_must_not_pull_dependencies(self):
        self.assertFlagged("pip install /wheels/*.whl", "without --no-deps")
        self.assertFlagged('pip install "$(ls /wheels/*.whl)[api]"', "extras")
        self.assertFlagged(
            "pip install --no-deps /wheels/*.whl", "without --no-build-isolation"
        )
        self.assertEqual(
            run("pip install --no-deps --no-build-isolation /wheels/*.whl"), []
        )

    def test_source_install_needs_flags_and_build_prerequisites_first(self):
        self.assertFlagged("pip install .", "without --no-deps")
        self.assertFlagged('pip install ".[api]"', "extras")
        self.assertFlagged(
            "pip install --no-deps --no-build-isolation .", "build prerequisites"
        )
        self.assertFlagged(
            "pip install --require-hashes -r requirements/build.txt"
            " --no-deps --no-build-isolation .",
            "mixed",
        )
        ok = (
            "FROM py AS build\n"
            + GOOD_BUILD
            + ("RUN pip wheel --no-deps --no-build-isolation --wheel-dir /w .\n")
        )
        self.assertEqual(rules(ok), [])
        # Another stage does not inherit the prerequisites ...
        other = (
            ok + "FROM py AS second\nRUN pip install --no-deps --no-build-isolation .\n"
        )
        self.assertTrue(any("build prerequisites" in r for r in rules(other)))
        # ... unless it is built FROM that stage.
        child = (
            ok
            + "FROM build AS child\nRUN pip install --no-deps --no-build-isolation .\n"
        )
        self.assertEqual(rules(child), [])

    def test_renamed_or_hidden_installers_are_flagged(self):
        for text in (
            "PIP=pip; $PIP install requests",
            '"${INSTALLER}" install requests',
            "$PY -m $MOD install requests",
            "alias getpkg=pip",
            '"p""ip" install requests',
            "p\\ip install requests",
            'python -c "import pip; pip.main([chr(105)])"',
            'cp "$(command -v pip)" /usr/local/bin/getpkg',
            "ln -s /usr/local/bin/pip3 /usr/local/bin/getpkg",
            "uvx ruff check .",
            "uv tool install ruff",
            "uv pip install ruff",
            "uv --quiet pip install ruff",
            "easy_install requests",
            "echo pip install requests | sh",
            "sudo -H pip install requests",
            "timeout 60 pip install requests",
        ):
            with self.subTest(text=text):
                self.assertTrue(run(text), text)

    def test_exec_form_and_continuations(self):
        self.assertTrue(rules('FROM py\nRUN ["pip", "install", "requests"]\n'))
        self.assertTrue(rules("FROM py\nRUN pip install \\\n    requests\n"))
        self.assertTrue(
            rules("FROM py\nRUN --mount=type=cache,target=/c pip install x\n")
        )

    def test_compliant_and_unrelated_commands_pass(self):
        for text in (
            "pip install --no-cache-dir --require-hashes -r requirements/api.txt",
            "python -m pip install --require-hashes -r helper/requirements/build.txt"
            " -r helper/requirements/api.txt",
            "python -m pip freeze --exclude-editable > resolved-requirements.txt",
            "python -m pip check",
            "pip-audit -r frozen.txt --no-deps --disable-pip",
            "uv pip compile --universal --generate-hashes requirements/ci.in -o x.txt",
            "apt-get install -y --no-install-recommends git",
            "python -m ensurepip",
            "pip install --require-hashes -r requirements/ci.txt  # pins ruff==0.16.9",
        ):
            with self.subTest(text=text):
                self.assertEqual(run(text), [])


class SourceShapeTests(unittest.TestCase):
    def test_workflow_run_steps_are_scanned(self):
        text = (
            "jobs:\n  a:\n    steps:\n"
            "      - uses: actions/setup-python@x\n        with:\n          cache: pip\n"
            "      - run: python -m pip install ruff\n"
            "      - name: block\n        run: |\n          echo hi\n"
            "          python -m pip install \\\n            coverage\n"
        )
        found = c.scan_text(".github/workflows/x.yml", text)
        self.assertEqual(sorted({f.line for f in found}), [7, 11])

    def test_markdown_code_blocks_and_prose(self):
        text = (
            "Run `pip install requests` first.\n"
            "No pip caching here, and pip-audit is a tool.\n"
            "```sh\npip install --no-deps --no-build-isolation .\n```\n"
            "```sh\n"
            + GOOD_BUILD[4:]
            + "pip install --no-deps --no-build-isolation .\n```\n"
        )
        found = c.scan_text("docs/X.md", text)
        self.assertEqual(sorted({f.line for f in found}), [1, 4])

    def test_sources_cover_dockerfiles_yaml_and_docs(self):
        self.assertEqual(c.source_kind("Dockerfile.render"), "docker")
        self.assertEqual(c.source_kind("sub/Containerfile"), "docker")
        self.assertEqual(c.source_kind(".github/workflows/new.yaml"), "yaml")
        self.assertEqual(c.source_kind(".pre-commit-config.yaml"), "yaml")
        self.assertEqual(c.source_kind("docs/NEW.md"), "doc")
        self.assertIsNone(c.source_kind("tests/fixtures/ci_summary_288/full.md"))


class PendingWorkflowTests(unittest.TestCase):
    def findings(self):
        return [
            c.Finding(".github/workflows/t.yml", 3, "pip install x", "r1"),
            c.Finding(".github/workflows/t.yml", 3, "pip install x", "r2"),
            c.Finding(".github/workflows/t.yml", 9, "pip install x", "r1"),
            c.Finding("Dockerfile", 2, "pip install y", "r1"),
        ]

    def test_exact_entry_is_pending_and_others_still_fail(self):
        entry = {
            "path": ".github/workflows/t.yml",
            "command": "pip install x",
            "count": 2,
        }
        violations, pending, errors = c.apply_pending(self.findings(), [entry])
        self.assertEqual(errors, [])
        self.assertEqual({f.path for f in violations}, {"Dockerfile"})
        self.assertEqual(len(pending), 3)

    def test_stale_or_miscounted_entry_fails(self):
        for count in (1, 3):
            entry = {
                "path": ".github/workflows/t.yml",
                "command": "pip install x",
                "count": count,
            }
            _, _, errors = c.apply_pending(self.findings(), [entry])
            self.assertTrue(errors)
        gone = {
            "path": ".github/workflows/t.yml",
            "command": "pip install z",
            "count": 1,
        }
        self.assertTrue(c.apply_pending(self.findings(), [gone])[2])

    def test_only_workflow_paths_may_be_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements").mkdir()
            (root / c.PENDING).write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "path": "Dockerfile",
                                "command": "pip install y",
                                "count": 1,
                            }
                        ]
                    }
                )
            )
            entries, errors = c.load_pending(root)
            self.assertTrue(errors)
            violations, _, _ = c.apply_pending(self.findings(), entries)
            self.assertIn("Dockerfile", {f.path for f in violations})


@unittest.skipUnless(HAS_LOCKS, "the locks are not in the Docker test stage")
class LockEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copy(ROOT / "pyproject.toml", self.root / "pyproject.toml")
        shutil.copytree(ROOT / "requirements", self.root / "requirements")
        self.tracked = sorted(
            p.relative_to(self.root).as_posix()
            for p in self.root.rglob("*")
            if p.is_file()
        )

    def tearDown(self):
        self.tmp.cleanup()

    def errors(self):
        return c.check_locks(self.root, self.tracked)

    def edit(self, rel, old, new):
        path = self.root / rel
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def assertError(self, needle):
        found = self.errors()
        self.assertTrue(any(needle in e for e in found), found)

    def test_committed_locks_pass(self):
        self.assertEqual(self.errors(), [])

    def test_lock_version_edited_without_its_hash(self):
        self.edit("requirements/ci.txt", "filelock==4.2.9", "filelock==4.2.10")
        self.assertError("changed since its recorded evidence")
        self.assertError("disagrees with requirements/api.txt")

    def test_hash_mismatch_is_detected(self):
        text = (self.root / "requirements/api.txt").read_text(encoding="utf-8")
        start = text.index("filelock==")
        digest = text.index("--hash=sha256:", start) + len("--hash=sha256:")
        flipped = "0" if text[digest] != "0" else "1"
        (self.root / "requirements/api.txt").write_text(
            text[:digest] + flipped + text[digest + 1 :], encoding="utf-8"
        )
        self.assertError("requirements/api.txt: changed since its recorded evidence")
        self.assertError("filelock disagrees with requirements/api.txt")

    def test_pin_without_hash_and_unpinned_lines(self):
        path = self.root / "requirements/build.txt"
        path.write_text(path.read_text(encoding="utf-8") + "extra-tool\nother==1.0\n")
        self.assertError("not an exact name==version pin")
        self.assertError("other: pin carries no --hash")

    def test_option_lines_are_refused(self):
        path = self.root / "requirements/build.txt"
        path.write_text(
            "--extra-index-url https://example.invalid/simple\n" + path.read_text()
        )
        self.assertError("option line not allowed")

    def test_input_changed_without_relock(self):
        self.edit(
            "pyproject.toml",
            '"filelock>=3.13,<5",',
            '"filelock>=3.13,<5",\n    "synthetic-new-dep>=1",',
        )
        self.assertError("requirements/api.txt: its inputs changed since it was locked")
        self.assertError("top-level requirement synthetic-new-dep is not pinned")

    def test_pin_out_of_declared_range(self):
        self.edit("pyproject.toml", '"Pillow>=13,<14"', '"Pillow>=14,<15"')
        self.assertError("does not satisfy")

    def test_build_prerequisites_match_build_system(self):
        self.edit(
            "pyproject.toml",
            'requires = ["setuptools>=68"]',
            'requires = ["setuptools>=69"]',
        )
        self.assertError("differs from [build-system] requires")

    def test_unknown_requirements_file(self):
        self.tracked.append("requirements/extra.txt")
        self.assertError(
            "requirements/extra.txt: requirements file is not a maintained lock"
        )

    def test_missing_evidence_or_command_header(self):
        self.edit("requirements/build.txt", "--generate-hashes", "--no-hashes")
        self.assertError("header does not show the generating command")
        (self.root / c.EVIDENCE).unlink()
        self.assertError("missing; run --write-evidence")

    def test_written_evidence_matches_committed(self):
        committed = json.loads((ROOT / c.EVIDENCE).read_text(encoding="utf-8"))
        self.assertEqual(c.build_evidence(ROOT), committed)


class LockParserTests(unittest.TestCase):
    def test_markers_hashes_and_duplicates(self):
        h = "--hash=sha256:" + "a" * 64
        pins, errors = c.parse_lock(
            f"x==1.0 ; python_full_version < '3.12' \\\n    {h}\n"
            f"x==2.0 ; python_full_version >= '3.12' \\\n    {h}\n",
            "t",
        )
        self.assertEqual(errors, [])
        self.assertEqual([p.version for p in pins], ["1.0", "2.0"])
        _, errors = c.parse_lock(
            f"x==1.0 {h} {h}\nx==1.0 {h}\ny==1 --hash=md5:ab\n", "t"
        )
        self.assertEqual(
            [e.split(": ", 1)[1] for e in errors],
            [
                "x: duplicate hash",
                "x: pinned twice for one marker",
                "malformed hash option: --hash=md5:ab",
                "y: pin carries no --hash",
            ],
        )

    def test_specifier_comparison_rejects_unknown_operators(self):
        self.assertTrue(c.satisfies("13.0.0", ">=13,<14"))
        self.assertFalse(c.satisfies("14.0", ">=13,<14"))
        with self.assertRaises(ValueError):
            c.satisfies("1.0", "~=1.0")


@unittest.skipUnless(SOURCE_CHECKOUT, "needs the Dockerfiles and workflows")
class RepositoryTests(unittest.TestCase):
    def test_repository_installs_are_hash_locked(self):
        violations, _ = c.check(ROOT)
        self.assertEqual(violations, [])

    def test_every_install_source_is_scanned(self):
        tracked = c.tracked_files(ROOT)
        scanned = {rel for rel in tracked if c.source_kind(rel)}
        for rel in ("Dockerfile", "Dockerfile.render", "README.md", "CONTRIBUTING.md",
                    ".pre-commit-config.yaml", "docs/STYLE.md", "docs/API106.md",
                    "docs/private-runner.yml"):  # fmt: skip
            self.assertIn(rel, scanned)
        workflows = {rel for rel in tracked if rel.startswith(".github/workflows/")}
        self.assertTrue(workflows)
        self.assertLessEqual(workflows, scanned)

    def test_pending_entries_name_exactly_the_current_workflow_lines(self):
        findings = []
        for rel in c.tracked_files(ROOT):
            if c.source_kind(rel):
                findings += c.scan_text(rel, (ROOT / rel).read_text(encoding="utf-8"))
        entries, errors = c.load_pending(ROOT)
        self.assertEqual(errors, [])
        violations, pending, errors = c.apply_pending(findings, entries)
        self.assertEqual((violations, errors), ([], []))
        workflow = {
            (f.path, f.text)
            for f in findings
            if f.path.startswith(".github/workflows/")
        }
        self.assertEqual({(f.path, f.text) for f in pending}, workflow)
        self.assertEqual({(e["path"], e["command"]) for e in entries}, workflow)

    def test_docker_test_stage_still_copies_what_it_installs(self):
        text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY requirements/build.txt", text)
        self.assertIn("COPY requirements/api.txt", text)


if __name__ == "__main__":
    unittest.main()
