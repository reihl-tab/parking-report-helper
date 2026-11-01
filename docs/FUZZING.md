# Fuzzing (#180)

`scripts/fuzz_parsers.py` is a bounded, stdlib-only mutation fuzzer for the
surfaces that read untrusted files: `stats.load_incidents` (export JSON),
`cli._read_json` (reporter/cars JSON), `intake.read_intake` (report.json
records) and `stats.parse_datetime`.

Contract: parsers may fail with `ValueError`/`OSError` (including
`JSONDecodeError`/`UnicodeError`); any other exception is a crash. Crash
inputs are saved to `tests/fuzz_corpus/<target>/` and replayed by
`tests/test_fuzz_corpus.py`, so a found bug becomes a permanent regression
test. Runs are deterministic (`--seed`, default 180) and wall-clock bounded
(`--seconds`, default 30 per target). No network, credentials or real
report data: seeds are synthetic and mutations are local.

```sh
python scripts/fuzz_parsers.py --seconds 60          # all targets
python scripts/fuzz_parsers.py --target stats-export --seed 42
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
