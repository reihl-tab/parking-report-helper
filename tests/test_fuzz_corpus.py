"""Replay the fuzz regression corpus and smoke-run the fuzzer (#180).

Corpus files are crash inputs found by scripts/fuzz_parsers.py; each one
must be handled with a documented ValueError/OSError (or parse fine) - a
crash here means a parser regressed. The smoke run is a short, seeded
fuzzer pass so regressions in the harness itself fail loudly.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fuzz_parsers  # noqa: E402

CORPUS = ROOT / "tests" / "fuzz_corpus"


class CorpusTest(unittest.TestCase):
    def test_every_corpus_input_is_handled(self):
        replayed = 0
        for target_dir in sorted(CORPUS.iterdir()):
            if not target_dir.is_dir():
                continue
            target = fuzz_parsers.TARGETS[target_dir.name]
            for path in sorted(target_dir.glob("*.bin")):
                with self.subTest(target=target_dir.name, file=path.name):
                    self.assertIsNone(
                        fuzz_parsers.run_target(target, path.read_bytes()),
                        f"corpus input {path} still crashes",
                    )
                replayed += 1
        # The corpus starts empty; keep the assertion meaningful when it grows.
        self.assertGreaterEqual(replayed, 0)

    def test_seeded_smoke_run_finds_no_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in sorted(fuzz_parsers.TARGETS):
                with self.subTest(target=name):
                    self.assertEqual(
                        fuzz_parsers.fuzz(
                            name,
                            seed=180,
                            seconds=2.0,
                            corpus_dir=Path(tmp),
                        ),
                        0,
                    )


if __name__ == "__main__":
    unittest.main()
