# Dependency locks (#177)

Every Python install in this repository reads a hash-locked requirements
file, so a changed or substituted package fails the install instead of
running. It answers the Scorecard pinned-dependency alerts for `pip`
installs in the Dockerfiles and the workflows (#177, parent #43).

## The locks

| Lock | Generated from | Used by |
|---|---|---|
| `requirements/build.txt` | `requirements/build.in` (= `[build-system] requires`) | every install of the project from source, before `--no-build-isolation` |
| `requirements/api.txt` | `pyproject.toml`, `api` extra | Docker `test`/`api`/`http` stages, `Dockerfile.render`, tests, mypy, coverage, security scan |
| `requirements/ci.txt` | `requirements/ci.in` (ruff, mypy, coverage, bandit, pip-audit), constrained by `requirements/api.txt` | CI tool jobs and local lint |
| `requirements/dev.txt` | `requirements/dev.in` (pre-commit), constrained by `requirements/api.txt` and `requirements/ci.txt` | local hooks only |

The core CLI has no third-party dependencies, so it has no runtime lock of its
own: it needs only `requirements/build.txt` to build.

Each lock is one universal lock (`uv pip compile --universal`) with the
Python 3.11 floor from `requires-python`. Environment markers carry the
differences between Python 3.11, 3.12 and 3.13 (today `pyproj` is pinned to one
release below 3.12 and another from 3.12), and the hashes cover every
published file for Linux, macOS and Windows. One file per purpose is easier
to review than one per Python version and platform, and it cannot drift
between versions. Constrained locks pin a shared package to the same version
and hashes, so `requirements/api.txt` and `requirements/ci.txt` install
together.

## Installing

```sh
python -m pip install --require-hashes -r requirements/build.txt -r requirements/api.txt
python -m pip install --no-deps --no-build-isolation .
python -m pip install --require-hashes -r requirements/ci.txt
```

The first line is the only one that fetches from the network, and every file it
fetches is checked against a sha256. The second builds the project with the
already-installed, pinned setuptools and installs nothing else. The third is
for the lint, type-check, coverage and security tools. Wheels from
`pip wheel` install the same way, with `--no-deps --no-build-isolation`.

## Re-locking

Run from the repository root with uv, then record the new evidence:

```sh
uv pip compile --universal --generate-hashes --python-version 3.11 requirements/build.in -o requirements/build.txt
uv pip compile --universal --generate-hashes --python-version 3.11 pyproject.toml --extra api -o requirements/api.txt
uv pip compile --universal --generate-hashes --python-version 3.11 requirements/ci.in -c requirements/api.txt -o requirements/ci.txt
uv pip compile --universal --generate-hashes --python-version 3.11 requirements/dev.in -c requirements/api.txt -c requirements/ci.txt -o requirements/dev.txt
python scripts/check_install_pins.py --write-evidence
python scripts/check_install_pins.py
```

Re-lock in this order, because `ci` and `dev` are constrained by the locks
above them. Add `--upgrade` (or `--upgrade-package NAME`) to take newer
releases. Review the lock diff like code. A change to the `api` extra or
`[build-system]` in `pyproject.toml` must re-lock in the same PR.

## The guard

`scripts/check_install_pins.py` (standard library, offline) runs in the test
suite (`tests/test_install_pins.py`). It scans every Dockerfile, every YAML
`run:` step and the documented commands in tracked Markdown and
`.pre-commit-config.yaml`, and fails on:

- `pip` or `python -m pip` (global options included), `uv pip`, `pipx`,
  `uvx` or `easy_install` installing a named, unpinned, tag-only or URL
  requirement;
- a requirements install without `--require-hashes`, or from a file that is
  not one of the locks above;
- the project or a wheel installed without `--no-deps --no-build-isolation`,
  with extras, or built from source before `requirements/build.txt`;
- an installer reached through a variable or another name.

It also checks the locks: exact pins, a sha256 for every pin, the declared
top-level requirements present and in range, shared pins identical across
constrained locks, the generating command in each header, and the digests in
`requirements/lock-evidence.json`. A lock edited by hand, or an input changed
without re-locking, fails.

### Pending workflow lines

The workflow install steps are changed by the maintainer in a separate,
coordinated change. Until then, `requirements/pending-workflow-installs.json`
names each unconverted line under `.github/workflows/`, with its exact text and
count. The guard still scans the workflows and prints these lines as pending.
It fails on any new violating line, and on an entry that no longer matches
exactly. Entries outside `.github/workflows/` are refused. The workflow change
deletes the file.
