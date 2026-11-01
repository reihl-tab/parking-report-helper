# Fuzz regression corpus

Crash inputs found by `scripts/fuzz_parsers.py` (#180), one subdirectory per
target. Every file here must be handled by its parser with a documented
`ValueError`/`OSError` (or a successful parse) - replayed by
`tests/test_fuzz_corpus.py`. Empty means: no known crashers.
