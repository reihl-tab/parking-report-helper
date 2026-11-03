#!/usr/bin/env python3
"""Bounded, stdlib-only mutation fuzzer for untrusted parser inputs (#180).

Targets the surfaces that read untrusted files:

- ``stats-export``: ``stats.load_incidents`` on a mutated export file;
- ``cars-json``: ``cli._read_json`` plus the downstream
  ``municipality.build_submissions`` parse path on a mutated file;
- ``intake-record``: ``intake.read_intake`` on a tree whose report.json is
  mutated;
- ``parse-datetime``: ``stats.parse_datetime`` on mutated text.

Contract: parsers may fail with ``ValueError``/``OSError`` (including
``json.JSONDecodeError`` and ``UnicodeError`` subclasses); anything else -
``TypeError``, ``KeyError``, ``IndexError``, ``RecursionError`` - is a
crash: the offending input is saved to the corpus directory and the run
exits 1. Deterministic given ``--seed``; bounded by ``--iterations`` when
given (fully deterministic, machine-independent), otherwise wall-clock
bounded by ``--seconds``. The two bounds are mutually exclusive, and
``--iterations`` must be at least 1; either mistake is a usage error (exit
2) before any fuzzing runs (#246). No network, no credentials, synthetic
inputs only.
"""

from __future__ import annotations

import argparse
import hashlib
import random
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from parking_report import cli, intake, municipality, stats  # noqa: E402

ALLOWED = (ValueError, OSError)

INTERESTING = (
    b"\x00",
    b"\xff\xff",
    b'"',
    b"\\",
    b"{}",
    b"[]",
    b"null",
    b"1e999",
    b'"\\uD800"',
    b'{"incidents":',
    b"\r\n\r\n",
)

SEEDS: dict[str, list[bytes]] = {
    "stats-export": [
        b'{"incidents": []}',
        b'{"incidents": [{"caseId": "a", "createdon": "2026-05-01T08:00:00"}]}',
        b"[]",
        b"{}",
    ],
    "cars-json": [
        b"[]",
        b"[{}]",
        b'[{"plate": "synthetic"}]',
        b"{}",
        # Structured edge: a non-string draft_kind must raise ValueError, not
        # TypeError (unhashable membership probe), post-#245 review.
        b'[{"draft_kind": []}]',
        # A fully valid car, so mutations also start from the success path
        # through build_submissions rather than only error paths.
        b'[{"plate": "123-45-678", "street": "synthetic street 1", '
        b'"datetime": "2026-01-01T10:00"}]',
    ],
    "intake-record": [b"{}", b'{"cars": []}', b"null", b'{"plate": "x"}'],
    "parse-datetime": [
        b"2026-05-01T08:00:00",
        b"2026-13-45T99:99:99Z",
        b"",
        b"2026-05-01T08:00:00+02:00",
    ],
}


def target_stats_export(data: bytes) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "export.json"
        path.write_bytes(data)
        stats.load_incidents([path])


_CARS_CONFIG: dict | None = None


def target_cars_json(data: bytes) -> None:
    global _CARS_CONFIG
    if _CARS_CONFIG is None:
        _CARS_CONFIG = municipality.load_config("jerusalem")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "cars.json"
        path.write_bytes(data)
        cars = cli._read_json(str(path))
    # Downstream parsing path, not just JSON loading (#227). Both photo
    # modes: with_photo only changes the built description text.
    municipality.build_submissions(cars, _CARS_CONFIG, {}, False)
    municipality.build_submissions(cars, _CARS_CONFIG, {}, True)


def target_intake_record(data: bytes) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "case-1"
        folder.mkdir()
        (folder / intake.RECORD_NAME).write_bytes(data)
        intake.read_intake(tmp)


def target_parse_datetime(data: bytes) -> None:
    stats.parse_datetime(data.decode("utf-8", errors="replace"))


TARGETS: dict[str, Callable[[bytes], None]] = {
    "stats-export": target_stats_export,
    "cars-json": target_cars_json,
    "intake-record": target_intake_record,
    "parse-datetime": target_parse_datetime,
}


def mutate(rng: random.Random, data: bytes) -> bytes:
    for _ in range(rng.randint(1, 8)):
        choice = rng.randint(0, 5)
        if choice == 0 and data:
            pos = rng.randrange(len(data))
            data = data[:pos] + bytes([rng.randrange(256)]) + data[pos + 1 :]
        elif choice == 1 and data:
            pos = rng.randrange(len(data))
            data = data[:pos] + data[pos + 1 :]
        elif choice == 2:
            pos = rng.randrange(len(data) + 1)
            data = data[:pos] + bytes([rng.randrange(256)]) + data[pos:]
        elif choice == 3 and data:
            start = rng.randrange(len(data))
            end = rng.randrange(start, len(data) + 1)
            pos = rng.randrange(len(data) + 1)
            data = data[:pos] + data[start:end] + data[pos:]
        elif choice == 4 and data:
            data = data[: rng.randrange(len(data) + 1)]
        else:
            pos = rng.randrange(len(data) + 1)
            token = rng.choice(INTERESTING)
            data = data[:pos] + token + data[pos:]
    return data


def run_target(target: Callable[[bytes], None], data: bytes) -> str | None:
    """Return the crash exception class name, or None when input is handled."""
    try:
        target(data)
    except ALLOWED:
        return None
    except Exception as exc:  # noqa: BLE001 - the crash IS the finding
        return type(exc).__name__
    return None


def save_crasher(corpus_dir: Path, target_name: str, data: bytes) -> Path:
    digest = hashlib.sha1(data).hexdigest()[:16]
    path = corpus_dir / target_name / f"{digest}.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def fuzz(
    target_name: str,
    *,
    seed: int,
    seconds: float | None = None,
    iterations: int | None = None,
    corpus_dir: Path,
    max_crashes: int = 20,
) -> int:
    if seconds is None and iterations is None:
        raise ValueError("pass seconds or iterations")
    rng = random.Random(f"{seed}:{target_name}")
    target = TARGETS[target_name]
    seeds = SEEDS[target_name]
    deadline = None if seconds is None else time.monotonic() + seconds
    done = 0
    crashes: list[str] = []
    while len(crashes) < max_crashes:
        if deadline is not None and time.monotonic() >= deadline:
            break
        if iterations is not None and done >= iterations:
            break
        data = mutate(rng, rng.choice(seeds))
        crash = run_target(target, data)
        done += 1
        if crash is not None:
            path = save_crasher(corpus_dir, target_name, data)
            crashes.append(f"{crash} -> {path}")
    print(
        f"fuzz[{target_name}]: {done} iterations, "
        f"{len(crashes)} crash(es) (seed={seed})"
    )
    for line in crashes:
        print(f"fuzz[{target_name}]: CRASH {line}", file=sys.stderr)
    return 1 if crashes else 0


def positive_int(text: str) -> int:
    """argparse type for ``--iterations``: an integer >= 1 (#246)."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid int value: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {value}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fuzz_parsers.py", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--target", choices=sorted(TARGETS), action="append")
    parser.add_argument("--seed", type=int, default=180)
    # Mutually exclusive (#246): an explicit --seconds with --iterations used
    # to be silently ignored. Both default to None, so either flag alone (or
    # neither, meaning 30 s per target) behaves exactly as before.
    bound = parser.add_mutually_exclusive_group()
    bound.add_argument(
        "--seconds",
        type=float,
        default=None,
        help="wall-clock bound per target (default 30 when neither "
        "--seconds nor --iterations is given)",
    )
    bound.add_argument(
        "--iterations",
        type=positive_int,
        default=None,
        help="fixed iteration count per target, >= 1 (deterministic, "
        "machine-independent); cannot be combined with --seconds",
    )
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=Path("tests/fuzz_corpus"),
        help="where crash inputs are saved",
    )
    args = parser.parse_args(argv)
    status = 0
    for name in args.target or sorted(TARGETS):
        seconds = args.seconds
        if seconds is None and args.iterations is None:
            seconds = 30.0
        status |= fuzz(
            name,
            seed=args.seed,
            seconds=None if args.iterations is not None else seconds,
            iterations=args.iterations,
            corpus_dir=args.corpus_dir,
        )
    return status


if __name__ == "__main__":
    sys.exit(main())
