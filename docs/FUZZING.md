# Fuzzing (#180)

`scripts/fuzz_parsers.py` is a bounded, stdlib-only mutation fuzzer for the
surfaces that read untrusted files: `stats.load_incidents` (export JSON),
`cli._read_json` (reporter/cars JSON), `intake.read_intake` (report.json
records) and `stats.parse_datetime`.

Contract: parsers may fail with `ValueError`/`OSError` (including
`JSONDecodeError`/`UnicodeError`); any other exception is a crash. Crash
inputs are saved to `tests/fuzz_corpus/<target>/` and replayed by
`tests/test_fuzz_corpus.py`, so a found bug becomes a permanent regression
test. Mutations are deterministic for a given `--seed` (default 180). Each
target's run is bounded either by wall clock (`--seconds`, default 30 per
target when no bound is given) or by a fixed count (`--iterations`, which
makes the whole run machine-independent). The two bounds are mutually
exclusive and `--iterations` must be at least 1: `--seconds` together with
`--iterations`, or `--iterations 0`/negative, is a usage error (exit 2)
before anything is fuzzed (#246). No network, credentials or real report
data: seeds are synthetic and mutations are local.

```sh
python scripts/fuzz_parsers.py --seconds 60          # all targets
python scripts/fuzz_parsers.py --target stats-export --seed 42
python scripts/fuzz_parsers.py --iterations 200      # deterministic count
```

## Honest scope

This is a local, in-repo fuzzer. It is **not** a recognized fuzzer
integration in the OpenSSF Scorecard sense (no OSS-Fuzz/ClusterFuzz, no
coverage-guided engine such as Atheris), so the Scorecard "no recognized
fuzzer" finding stays accurate until such an integration exists; this
harness narrows the gap but does not claim the check. Unit tests alone are
not described as fuzzing; the corpus replay is a regression mechanism, the
mutation runs are the fuzzing. CI wiring (a bounded fuzz job) needs
approval and a minutes review before it is added to a workflow.
