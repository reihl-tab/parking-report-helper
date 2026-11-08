"""Offline guard for hash-locked Python installs (#177).

Two checks, standard library only, no network:

1. Install commands. Every Dockerfile, every YAML `run:` step (the workflows
   under .github/workflows and docs/private-runner.yml) and the documented dev
   commands (tracked Markdown and .pre-commit-config.yaml) are scanned for
   pip, `python -m pip` (global options included), pipx, uv and easy_install.
   A network install must read a maintained lock with `--require-hashes`; the
   local project or a wheel installs only with `--no-deps --no-build-isolation`
   and, when built from source, after the pinned build prerequisites
   (requirements/build.txt). Named, unpinned, tag-only and URL installs fail.
   Installers reached through a variable or another name fail too, so a
   renamed command cannot step around the scan.
2. Lock evidence. Each maintained lock under requirements/ must carry only
   exact pins with sha256 hashes, cover its declared top-level requirements,
   agree pin-for-pin with the locks it was constrained by, show its generating
   command in its header, and match the digests recorded in
   requirements/lock-evidence.json for itself and for its inputs.

Workflow lines the maintainer has not yet converted may be listed in
requirements/pending-workflow-installs.json; each entry must name a current
violating line exactly (stale entries fail), and only .github/workflows paths
are accepted. See docs/DEPENDENCY-LOCKS.md.

Usage:
  python scripts/check_install_pins.py [--root DIR]
  python scripts/check_install_pins.py --write-evidence   # after re-locking
Exit 0 when everything conforms, 1 with one line per violation otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

UV_FLAGS = "uv pip compile --universal --generate-hashes --python-version 3.11"


@dataclass(frozen=True)
class LockSpec:
    """One maintained lock: how it is generated and what it must cover."""

    command: str
    inputs: tuple[str, ...] = ()
    pyproject: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()


LOCKS: dict[str, LockSpec] = {
    "requirements/build.txt": LockSpec(
        command=f"{UV_FLAGS} requirements/build.in -o requirements/build.txt",
        inputs=("requirements/build.in",),
        pyproject=("build-system.requires",),
    ),
    "requirements/api.txt": LockSpec(
        command=f"{UV_FLAGS} pyproject.toml --extra api -o requirements/api.txt",
        pyproject=(
            "project.requires-python",
            "project.dependencies",
            "project.optional-dependencies.api",
        ),
    ),
    "requirements/ci.txt": LockSpec(
        command=(
            f"{UV_FLAGS} requirements/ci.in -c requirements/api.txt"
            " -o requirements/ci.txt"
        ),
        inputs=("requirements/ci.in",),
        constraints=("requirements/api.txt",),
    ),
    "requirements/dev.txt": LockSpec(
        command=(
            f"{UV_FLAGS} requirements/dev.in -c requirements/api.txt"
            " -c requirements/ci.txt -o requirements/dev.txt"
        ),
        inputs=("requirements/dev.in",),
        constraints=("requirements/api.txt", "requirements/ci.txt"),
    ),
}
BUILD_LOCK = "requirements/build.txt"
EVIDENCE = "requirements/lock-evidence.json"
PENDING = "requirements/pending-workflow-installs.json"
PENDING_PREFIX = ".github/workflows/"

# --------------------------------------------------------------------------
# Lock files


@dataclass(frozen=True)
class Pin:
    name: str
    version: str
    marker: str
    hashes: tuple[str, ...]
    line: int


_PIN = re.compile(
    r"^([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)==([A-Za-z0-9.+!_-]+)"
    r"(?:\s*;\s*(.+?))?\s*$"
)
_HASH = re.compile(r"^--hash=sha256:([0-9a-f]{64})$")


def normalize(name: str) -> str:
    """PEP 503 project-name normalization."""
    return re.sub(r"[-_.]+", "-", name).lower()


def logical_lines(text: str) -> list[tuple[int, str]]:
    """Join backslash-continued lines; return (first line number, text)."""
    out: list[tuple[int, str]] = []
    buf: list[str] = []
    start = 0
    for n, raw in enumerate(text.splitlines(), 1):
        if not buf:
            start = n
        if raw.endswith("\\"):
            buf.append(raw[:-1])
            continue
        buf.append(raw)
        out.append((start, " ".join(part.strip() for part in buf)))
        buf = []
    if buf:
        out.append((start, " ".join(part.strip() for part in buf)))
    return out


def parse_lock(text: str, label: str) -> tuple[list[Pin], list[str]]:
    """Parse a hash-locked requirements file; return pins and violations."""
    pins: list[Pin] = []
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for n, line in logical_lines(text):
        body = line.strip()
        if not body or body.startswith("#"):
            continue
        if body.startswith("-"):
            errors.append(f"{label}:{n}: option line not allowed in a lock: {body}")
            continue
        req, _, rest = body.partition("--hash=")
        match = _PIN.match(req.strip())
        if not match:
            errors.append(f"{label}:{n}: not an exact name==version pin: {req.strip()}")
            continue
        hashes: list[str] = []
        for token in ("--hash=" + rest).split() if rest else []:
            found = _HASH.match(token)
            if not found:
                errors.append(f"{label}:{n}: malformed hash option: {token}")
                continue
            hashes.append(found.group(1))
        name = normalize(match.group(1))
        if not hashes:
            errors.append(f"{label}:{n}: {name}: pin carries no --hash")
        if len(set(hashes)) != len(hashes):
            errors.append(f"{label}:{n}: {name}: duplicate hash")
        marker = match.group(3) or ""
        if (name, marker) in seen:
            errors.append(f"{label}:{n}: {name}: pinned twice for one marker")
        seen.add((name, marker))
        pins.append(Pin(name, match.group(2), marker, tuple(sorted(hashes)), n))
    return pins, errors


def _version_key(version: str) -> tuple[int, ...]:
    if not re.fullmatch(r"\d+(?:\.\d+)*", version):
        raise ValueError(version)
    parts = [int(p) for p in version.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def satisfies(version: str, specifier: str) -> bool:
    """Plain release versions against ==, !=, >=, <=, >, < clauses only.
    Anything else raises ValueError, so the caller rejects it."""
    have = _version_key(version)
    for clause in filter(None, (c.strip() for c in specifier.split(","))):
        match = re.fullmatch(r"(==|!=|>=|<=|>|<)\s*(\d+(?:\.\d+)*)", clause)
        if not match:
            raise ValueError(clause)
        want = _version_key(match.group(2))
        op = match.group(1)
        ok = {
            "==": have == want,
            "!=": have != want,
            ">=": have >= want,
            "<=": have <= want,
            ">": have > want,
            "<": have < want,
        }[op]
        if not ok:
            return False
    return True


_ROOT_REQ = re.compile(
    r"^([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(\[[^\]]*\])?\s*([^;]*)(?:;.*)?$"
)


def split_requirement(req: str) -> tuple[str, str]:
    match = _ROOT_REQ.match(req.strip())
    if not match:
        raise ValueError(req)
    return normalize(match.group(1)), match.group(3).strip()


def in_file_requirements(text: str) -> list[str]:
    reqs = []
    for _, line in logical_lines(text):
        body = line.split("#", 1)[0].strip()
        if body:
            reqs.append(body)
    return reqs


def pyproject_section(data: dict, dotted: str) -> object:
    node: object = data
    for key in dotted.split("."):
        if not isinstance(node, dict) or key not in node:
            raise KeyError(dotted)
        node = node[key]
    return node


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def inputs_digest(root: Path, spec: LockSpec, pyproject: dict) -> str:
    """Digest of everything a lock is generated from (inputs, the pyproject
    sections it reads and the constraint locks), canonical JSON."""
    parts: dict[str, object] = {"command": spec.command}
    for rel in spec.inputs + spec.constraints:
        path = root / rel
        parts[rel] = (
            sha256_text(path.read_text(encoding="utf-8")) if path.is_file() else None
        )
    for dotted in spec.pyproject:
        try:
            parts["pyproject.toml#" + dotted] = pyproject_section(pyproject, dotted)
        except KeyError:
            parts["pyproject.toml#" + dotted] = None
    return sha256_text(json.dumps(parts, sort_keys=True, ensure_ascii=True))


def build_evidence(root: Path) -> dict:
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    locks = {}
    for rel, spec in sorted(LOCKS.items()):
        text = (root / rel).read_text(encoding="utf-8")
        locks[rel] = {
            "command": spec.command,
            "inputs_sha256": inputs_digest(root, spec, pyproject),
            "lock_sha256": sha256_text(text),
        }
    return {
        "version": 1,
        "comment": (
            "Written by `python scripts/check_install_pins.py --write-evidence` "
            "after re-locking (docs/DEPENDENCY-LOCKS.md). The guard fails when a "
            "lock or one of its inputs changes without this file."
        ),
        "locks": locks,
    }


def _lock_roots(
    root: Path, spec: LockSpec, pyproject: dict
) -> tuple[list[str], list[str]]:
    """Top-level requirements a lock must cover (from .in files and
    pyproject sections), and errors reading them."""
    roots: list[str] = []
    errors: list[str] = []
    for inp in spec.inputs:
        inp_path = root / inp
        if not inp_path.is_file():
            errors.append(f"{inp}: lock input is missing")
            continue
        roots += in_file_requirements(inp_path.read_text(encoding="utf-8"))
    for dotted in spec.pyproject:
        try:
            value = pyproject_section(pyproject, dotted)
        except KeyError:
            errors.append(f"pyproject.toml: {dotted} missing")
            continue
        if isinstance(value, list):
            roots += [str(v) for v in value]
    return roots, errors


def _root_errors(rel: str, pins: dict[tuple[str, str], Pin], req: str) -> list[str]:
    """A top-level requirement must be pinned, in range, under every marker."""
    try:
        name, specifier = split_requirement(req)
    except ValueError:
        return [f"{rel}: cannot read top-level requirement {req!r}"]
    candidates = [p for (n, _), p in pins.items() if n == name]
    if not candidates:
        return [f"{rel}: top-level requirement {name} is not pinned"]
    errors = []
    for pin in candidates:
        try:
            ok = satisfies(pin.version, specifier)
        except ValueError:
            errors.append(f"{rel}: {name}: cannot compare {specifier!r}")
            continue
        if not ok:
            errors.append(
                f"{rel}:{pin.line}: {name}=={pin.version} does not satisfy"
                f" {specifier!r}"
            )
    return errors


def _lock_file_errors(
    root: Path, rel: str, spec: LockSpec, pyproject: dict
) -> tuple[dict[tuple[str, str], Pin], list[str]]:
    path = root / rel
    if not path.is_file():
        return {}, [f"{rel}: maintained lock is missing"]
    text = path.read_text(encoding="utf-8")
    pin_list, errors = parse_lock(text, rel)
    pins = {(p.name, p.marker): p for p in pin_list}
    header = "\n".join(text.splitlines()[:3])
    if f"#    {spec.command}" not in header:
        errors.append(f"{rel}: header does not show the generating command")
    roots, root_errors = _lock_roots(root, spec, pyproject)
    errors += root_errors
    for req in roots:
        errors += _root_errors(rel, pins, req)
    return pins, errors


def _build_in_errors(root: Path, pyproject: dict) -> list[str]:
    build_in = root / "requirements/build.in"
    if not build_in.is_file():
        return []
    declared = sorted(pyproject.get("build-system", {}).get("requires", []))
    locked = sorted(in_file_requirements(build_in.read_text(encoding="utf-8")))
    if declared == locked:
        return []
    return [
        "requirements/build.in: differs from [build-system] requires in pyproject.toml"
    ]


def _constraint_errors(parsed: dict[str, dict[tuple[str, str], Pin]]) -> list[str]:
    """A package pinned by a lock and by its constraint lock must agree."""
    errors = []
    for rel, spec in sorted(LOCKS.items()):
        for constraint in spec.constraints:
            for key, pin in sorted(parsed.get(rel, {}).items()):
                other = parsed.get(constraint, {}).get(key)
                if other and (other.version, other.hashes) != (pin.version, pin.hashes):
                    errors.append(
                        f"{rel}:{pin.line}: {pin.name} disagrees with {constraint}"
                        " (version or hashes)"
                    )
    return errors


def _evidence_errors(root: Path, pyproject: dict) -> list[str]:
    """Each lock, and what it was generated from, matches its evidence."""
    evidence_path = root / EVIDENCE
    if not evidence_path.is_file():
        return [f"{EVIDENCE}: missing; run --write-evidence after re-locking"]
    try:
        recorded = json.loads(evidence_path.read_text(encoding="utf-8"))["locks"]
        assert isinstance(recorded, dict)
    except (ValueError, KeyError, TypeError, AssertionError):
        return [f"{EVIDENCE}: unreadable"]
    errors = []
    if set(recorded) != set(LOCKS):
        errors.append(f"{EVIDENCE}: lists {sorted(recorded)}, expected {sorted(LOCKS)}")
    for rel, spec in sorted(LOCKS.items()):
        entry = recorded.get(rel) or {}
        path = root / rel
        if not path.is_file():
            continue
        if entry.get("lock_sha256") != sha256_text(path.read_text(encoding="utf-8")):
            errors.append(
                f"{rel}: changed since its recorded evidence (edited by hand?);"
                " re-lock, then --write-evidence"
            )
        if entry.get("inputs_sha256") != inputs_digest(root, spec, pyproject):
            errors.append(f"{rel}: its inputs changed since it was locked; re-lock")
    return errors


def check_locks(root: Path, tracked: list[str]) -> list[str]:
    """Violations in the maintained locks and their evidence."""
    pyproject_path = root / "pyproject.toml"
    if not pyproject_path.is_file():
        return ["pyproject.toml: missing"]
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    errors = [
        f"{rel}: requirements file is not a maintained lock"
        for rel in tracked
        if rel.startswith("requirements/") and rel.endswith(".txt") and rel not in LOCKS
    ]
    parsed: dict[str, dict[tuple[str, str], Pin]] = {}
    for rel, spec in sorted(LOCKS.items()):
        parsed[rel], lock_errors = _lock_file_errors(root, rel, spec, pyproject)
        errors += lock_errors
    errors += _build_in_errors(root, pyproject)
    errors += _constraint_errors(parsed)
    errors += _evidence_errors(root, pyproject)
    return errors


# --------------------------------------------------------------------------
# Install-command scanning


@dataclass
class Unit:
    """One logical command line to scan."""

    path: str
    line: int
    text: str
    code: bool  # False: prose (only explicit install shapes count)
    group: str  # build prerequisites must come earlier in the same group


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    text: str
    rule: str


_OPERATORS = {"&&", "||", ";", ";;", "|", "|&", "&", "(", ")", "{", "}", "!"}
_REDIRECTS = {">", ">>", "<", "<<", "<<<", ">&", "<&", "&>", "&>>", ">|"}
_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9_.-])(pip[0-9.]*|pipx|uvx|easy_install)(?![A-Za-z0-9_-])"
)
_PIP_HEAD = re.compile(r"^pip(?:\d+(?:\.\d+)*)?$")
_FETCHING = {"install", "download", "wheel"}
_PIP_GLOBAL_VALUE = {
    "--python", "--log", "--log-file", "--local-log", "--proxy", "--retries",
    "--timeout", "--exists-action", "--trusted-host", "--cert", "--client-cert",
    "--cache-dir", "--use-feature", "--use-deprecated", "--keyring-provider",
}  # fmt: skip
_PIP_VALUE = _PIP_GLOBAL_VALUE | {
    "-r", "--requirement", "-c", "--constraint", "-e", "--editable", "-t",
    "--target", "--prefix", "--root", "-i", "--index-url", "--extra-index-url",
    "-f", "--find-links", "--src", "--upgrade-strategy", "--python-version",
    "--platform", "--implementation", "--abi", "--global-option",
    "--config-settings", "-C", "--progress-bar", "--report", "--only-binary",
    "--no-binary", "--root-user-action", "-d", "--dest", "-w", "--wheel-dir",
    "--group", "-p", "--index-strategy", "--extra", "-o", "--output-file",
}  # fmt: skip
_PIPX_FETCHING = {
    "install", "install-all", "inject", "run", "runpip", "upgrade", "upgrade-all",
    "reinstall", "reinstall-all",
}  # fmt: skip


def tokenize(text: str, code: bool = True) -> list[str]:
    """Shell words; a `#` comment ends a code line. Unbalanced quotes (prose)
    fall back to whitespace splitting."""
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|()<>")
        lexer.whitespace_split = True
        lexer.commenters = "#" if code else ""
        tokens = list(lexer)
    except ValueError:
        tokens = text.split()
        if code and "#" in tokens:
            tokens = tokens[: tokens.index("#")]
    return [t.strip("`") for t in tokens if t.strip("`")]


def simple_commands(tokens: list[str]) -> list[list[str]]:
    commands: list[list[str]] = [[]]
    skip = False
    for i, tok in enumerate(tokens):
        if skip:
            skip = False
            continue
        if tok in _OPERATORS:
            commands.append([])
        elif tok in _REDIRECTS:
            if commands[-1] and commands[-1][-1].isdigit():
                commands[-1].pop()
            skip = True
        else:
            commands[-1].append(tok)
    return [c for c in commands if c]


def is_lock_path(value: str) -> str | None:
    """The maintained lock a requirements path names, by path suffix."""
    parts = [p for p in value.replace("\\", "/").split("/") if p not in ("", ".")]
    for rel in LOCKS:
        want = rel.split("/")
        if parts[-len(want) :] == want:
            return rel
    return None


@dataclass
class Parsed:
    options: set[str] = field(default_factory=set)
    requirement_files: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)


def parse_install_args(args: list[str]) -> Parsed:
    parsed = Parsed()
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--":
            parsed.targets += args[i + 1 :]
            break
        if tok.startswith("--") and "=" in tok:
            name, value = tok.split("=", 1)
            parsed.options.add(name)
            if name == "--requirement":
                parsed.requirement_files.append(value)
            elif name == "--editable":
                parsed.targets.append(value)
            i += 1
            continue
        if tok.startswith("-") and len(tok) > 1:
            short = tok[:2] if not tok.startswith("--") else tok
            if short in ("-r", "-c", "-e") and len(tok) > 2:
                value = tok[2:]
                parsed.options.add(short)
                if short == "-r":
                    parsed.requirement_files.append(value)
                elif short == "-e":
                    parsed.targets.append(value)
                i += 1
                continue
            parsed.options.add(tok)
            if tok in _PIP_VALUE and i + 1 < len(args):
                value = args[i + 1]
                if tok in ("-r", "--requirement"):
                    parsed.requirement_files.append(value)
                elif tok in ("-e", "--editable"):
                    parsed.targets.append(value)
                i += 2
                continue
            i += 1
            continue
        parsed.targets.append(tok)
        i += 1
    return parsed


def classify_target(target: str) -> str:
    """'wheel', 'source' (a local tree), 'url' or 'named'."""
    bare = re.sub(r"\[[^\]]*\]$", "", target)
    if "://" in bare or bare.startswith(("git+", "hg+", "svn+", "bzr+")):
        return "url"
    if ".whl" in bare:
        return "wheel"
    if (
        bare.startswith((".", "/", "~", "$"))
        or "/" in bare
        or bare.endswith((".tar.gz", ".zip"))
    ):
        return "source"
    return "named"


def _target_rules(
    target: str, parsed: Parsed, unit: Unit, state: dict[str, bool]
) -> list[str]:
    kind = classify_target(target)
    if kind == "url":
        return [f"URL/VCS install is tag-only, not hash-locked: {target}"]
    if kind == "named":
        pinned = "pinned" if "==" in target else "unpinned"
        return [f"{pinned} install without a hash lock: {target}"]
    rules = []
    if re.search(r"\[[^\]]*\]$", target):
        rules.append(f"extras on a local install pull in dependencies: {target}")
    for flag in ("--no-deps", "--no-build-isolation"):
        if flag not in parsed.options:
            rules.append(f"local install without {flag}: {target}")
    if kind == "source" and unit.code and not state.get(unit.group):
        rules.append(
            f"source install before the pinned build prerequisites ({BUILD_LOCK}):"
            f" {target}"
        )
    return rules


def check_install(
    tool: str, args: list[str], unit: Unit, state: dict[str, bool]
) -> list[str]:
    """Rules for one fetching pip / uv pip invocation (args after the verb)."""
    parsed = parse_install_args(args)
    files, targets = list(parsed.requirement_files), parsed.targets
    if tool == "uv-sync":
        files, targets = files + targets, []
    rules: list[str] = []
    for target in targets:
        rules += _target_rules(target, parsed, unit, state)
    if files and any(classify_target(t) in ("wheel", "source") for t in targets):
        rules.append("local install mixed with a requirements install")
    rules += [
        f"requirements file is not a maintained lock: {value}"
        for value in files
        if not is_lock_path(value)
    ]
    hashed = "--require-hashes" in parsed.options
    if files and not hashed:
        rules.append("requirements install without --require-hashes")
    if not files and not targets and unit.code:
        rules.append("install command with no recognizable target")
    if not rules and any(is_lock_path(v) == BUILD_LOCK for v in files):
        state[unit.group] = True
    return rules


def skip_options(tokens: list[str], start: int, valued: set[str]) -> int:
    i = start
    while i < len(tokens) and tokens[i].startswith("-"):
        tok = tokens[i]
        i += 2 if tok in valued and "=" not in tok else 1
    return i


_WRAPPERS = {
    "sudo", "env", "exec", "command", "time", "nice", "nohup", "xargs", "timeout",
    "stdbuf", "ionice", "then", "do", "else", "elif", "if", "while", "until",
}  # fmt: skip


def command_position(cmd: list[str]) -> int:
    """Index of the program a simple command runs, past assignments and
    wrappers such as `sudo` or `env` (and their options)."""
    i = 0
    while i < len(cmd):
        tok = cmd[i]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tok):
            i += 1
        elif tok.rsplit("/", 1)[-1] in _WRAPPERS:
            i += 1
            while i < len(cmd) and (cmd[i].startswith("-") or cmd[i].isdigit()):
                i += 1
        else:
            break
    return i


def _detect(
    cmd: list[str], i: int, unit: Unit, head: int, consumed: set[int]
) -> tuple[str | None, int, list[str]]:
    """Which installer token i starts, where its arguments begin, and any
    hidden-installer findings. In code a program name counts only where a
    program runs; elsewhere it is an indirect reference. Prose has no
    positions."""
    tok = cmd[i]
    base = tok.rsplit("/", 1)[-1].removesuffix(".exe")
    at_head = i == head or not unit.code
    nxt = cmd[i + 1] if i + 1 < len(cmd) else ""
    if at_head and _PIP_HEAD.match(base):
        return "pip", i + 1, []
    if at_head and base in ("pipx", "uv", "uvx", "easy_install"):
        return base, i + 1, []
    if re.fullmatch(r"-[A-Za-z]*mpip", tok):
        return "pip", i + 1, []
    if re.fullmatch(r"-[A-Za-z]*m", tok) and nxt:
        if _PIP_HEAD.match(nxt) or nxt in ("pip.__main__", "pipx", "uv"):
            consumed.add(i + 1)
            return (
                ("pip" if nxt.startswith("pip") and nxt != "pipx" else nxt),
                i + 2,
                [],
            )
        if nxt == "ensurepip":
            consumed.add(i + 1)
        elif nxt.startswith("$") and unit.code:
            return None, i + 1, [f"module name hidden behind a variable: {nxt}"]
        return None, i + 1, []
    if (
        tok.startswith("$")
        and unit.code
        and nxt in _FETCHING | {"sync", "inject", "-m"}
    ):
        return None, i + 1, [f"installer hidden behind a variable: {tok}"]
    return None, i + 1, []


def _uv_rules(
    cmd: list[str], rest: int, unit: Unit, state: dict[str, bool], consumed: set[int]
) -> list[str]:
    j = skip_options(cmd, rest, _PIP_GLOBAL_VALUE)
    verb = cmd[j] if j < len(cmd) else ""
    if verb == "pip":
        consumed.add(j)
        k = skip_options(cmd, j + 1, _PIP_GLOBAL_VALUE)
        sub = cmd[k] if k < len(cmd) else ""
        if sub in ("install", "sync"):
            tool = "uv" if sub == "install" else "uv-sync"
            return check_install(tool, cmd[k + 1 :], unit, state)
        return []
    if verb in ("tool", "add", "sync") or (verb == "run" and "--with" in cmd):
        return [f"uv {verb} does not install from a maintained hash lock"]
    return []


def _tool_rules(
    tool: str,
    cmd: list[str],
    rest: int,
    unit: Unit,
    state: dict[str, bool],
    consumed: set[int],
) -> list[str]:
    if tool == "pip":
        j = skip_options(cmd, rest, _PIP_GLOBAL_VALUE)
        if j < len(cmd) and cmd[j] in _FETCHING:
            return check_install("pip", cmd[j + 1 :], unit, state)
        return []
    if tool == "uv":
        return _uv_rules(cmd, rest, unit, state, consumed)
    if tool == "pipx":
        j = skip_options(cmd, rest, set())
        if j < len(cmd) and cmd[j] in _PIPX_FETCHING:
            return [f"pipx {cmd[j]} cannot verify hashes; use a lock"]
        return []
    if unit.code or len(cmd) > rest:  # uvx, easy_install
        return [f"{tool} does not install from a maintained hash lock"]
    return []


def scan_command(cmd: list[str], unit: Unit, state: dict[str, bool]) -> list[str]:
    rules: list[str] = []
    consumed: set[int] = set()
    head = command_position(cmd)
    for i in range(len(cmd)):
        if i in consumed:
            continue
        tool, rest, found = _detect(cmd, i, unit, head, consumed)
        rules += found
        if tool is not None:
            consumed.add(i)
            rules += _tool_rules(tool, cmd, rest, unit, state, consumed)
    if unit.code:
        for i, tok in enumerate(cmd):
            match = None if i in consumed else _REFERENCE.search(tok)
            if match:
                rules.append(f"installer referenced indirectly: {match.group(1)}")
    return rules


def scan_unit(unit: Unit, state: dict[str, bool]) -> list[str]:
    rules: list[str] = []
    for cmd in simple_commands(tokenize(unit.text, unit.code)):
        rules += scan_command(cmd, unit, state)
    return rules


def dockerfile_units(path: str, text: str) -> list[Unit]:
    units: list[Unit] = []
    stage = "stage0"
    names: dict[str, str] = {}
    count = 0
    for n, line in logical_lines(text):
        body = line.strip()
        if not body:
            continue
        if body.startswith("#"):
            units.append(Unit(path, n, body.lstrip("#").strip(), False, f"{path}#c{n}"))
            continue
        word, _, rest = body.partition(" ")
        word = word.upper()
        if word == "FROM":
            count += 1
            parts = rest.split()
            parent = names.get(parts[0].lower()) if parts else None
            stage = f"{path}#stage{count}"
            if len(parts) >= 3 and parts[-2].upper() == "AS":
                names[parts[-1].lower()] = stage
            units.append(Unit(path, n, "", True, stage + "|from|" + (parent or "")))
            continue
        if word == "RUN":
            while rest.startswith("--"):
                _, _, rest = rest.partition(" ")
            rest = rest.strip()
        if rest.startswith("["):
            try:
                array = json.loads(rest)
                if isinstance(array, list) and all(isinstance(a, str) for a in array):
                    rest = shlex.join(array)
            except ValueError:
                pass
        units.append(Unit(path, n, rest, True, stage))
    return units


_RUN_KEY = re.compile(r"^(\s*)(?:-\s+)?run:\s*(.*?)\s*$")
_JOB = re.compile(r"^  ([A-Za-z0-9_.-]+):\s*(?:#.*)?$")


def yaml_units(path: str, text: str) -> list[Unit]:
    units: list[Unit] = []
    lines = text.splitlines()
    job = path
    in_jobs = False
    i = 0
    while i < len(lines):
        raw = lines[i]
        n = i + 1
        if raw.strip().startswith("#"):
            units.append(Unit(path, n, raw.strip().lstrip("#").strip(), False, f"c{n}"))
            i += 1
            continue
        if re.match(r"^jobs:\s*$", raw):
            in_jobs = True
        elif in_jobs and _JOB.match(raw):
            job = f"{path}#{_JOB.match(raw).group(1)}"  # type: ignore[union-attr]
        elif raw and not raw.startswith(" "):
            in_jobs = raw.startswith("jobs:")
        match = _RUN_KEY.match(raw)
        if not match:
            i += 1
            continue
        indent = len(match.group(1))
        value = match.group(2)
        if re.fullmatch(r"[|>][-+0-9]*(?:\s+#.*)?", value):
            block: list[str] = []
            j = i + 1
            while j < len(lines) and (
                not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip()) > indent
            ):
                block.append(lines[j])
                j += 1
            for k, body in logical_lines("\n".join(block)):
                if body.strip():
                    units.append(Unit(path, n + k, body.strip(), True, job))
            i = j
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        units.append(Unit(path, n, value, True, job))
        i += 1
    return units


_FENCE = re.compile(r"^\s*(```+|~~~+)")
_SPAN = re.compile(r"`+([^`]+)`+")


def doc_units(path: str, text: str) -> list[Unit]:
    units: list[Unit] = []
    fence = None
    block = 0
    pending: list[str] = []
    start = 0
    for n, raw in enumerate(text.splitlines(), 1):
        match = _FENCE.match(raw)
        if fence is None and match:
            fence = match.group(1)[0] * 3
            block += 1
            continue
        if fence is not None and raw.strip().startswith(fence):
            fence = None
            continue
        if fence is None:
            body = raw.strip().lstrip("#").strip()
            # Each inline code span is its own command; the prose around it
            # is scanned separately, so a sentence never becomes arguments.
            for span in _SPAN.findall(body):
                units.append(Unit(path, n, span, False, f"{path}#p{n}"))
            rest = _SPAN.sub(" ; ", body).strip()
            if rest:
                units.append(Unit(path, n, rest, False, f"{path}#p{n}"))
            continue
        if not pending:
            start = n
        if raw.rstrip().endswith("\\"):
            pending.append(raw.rstrip()[:-1])
            continue
        pending.append(raw)
        body = " ".join(p.strip() for p in pending)
        pending = []
        if body.strip() and not body.strip().startswith("#"):
            units.append(Unit(path, start, body.strip(), True, f"{path}#b{block}"))
        elif body.strip():
            units.append(Unit(path, start, body.strip().lstrip("#"), False, f"c{n}"))
    return units


def source_kind(rel: str) -> str | None:
    base = rel.rsplit("/", 1)[-1]
    if base.startswith(("Dockerfile", "Containerfile")) or base.endswith(
        (".dockerfile", ".containerfile")
    ):
        return "docker"
    if base.endswith((".yml", ".yaml")):
        return "yaml"
    if base.endswith(".md") and not rel.startswith("tests/fixtures/"):
        return "doc"
    return None


def tracked_files(root: Path) -> list[str]:
    """Tracked paths (NUL-delimited git output), or a walk without git."""
    if (root / ".git").exists():
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            check=True,
            env=env,
            timeout=60,
        )
        return sorted(p for p in result.stdout.decode("utf-8").split("\0") if p)
    found = []
    for path in root.rglob("*"):
        rel = path.relative_to(root).as_posix()
        if path.is_file() and not rel.startswith(".git/"):
            found.append(rel)
    return sorted(found)


def units_for(rel: str, text: str) -> list[Unit]:
    kind = source_kind(rel)
    if kind == "docker":
        return dockerfile_units(rel, text)
    if kind == "yaml":
        return yaml_units(rel, text)
    if kind == "doc":
        return doc_units(rel, text)
    return []


def scan_text(rel: str, text: str) -> list[Finding]:
    """All install findings in one source file."""
    findings: list[Finding] = []
    state: dict[str, bool] = {}
    for unit in units_for(rel, text):
        if "|from|" in unit.group:
            stage, _, parent = unit.group.partition("|from|")
            state[stage] = bool(parent and state.get(parent))
            continue
        for rule in scan_unit(unit, state):
            findings.append(Finding(rel, unit.line, unit.text, rule))
    return findings


def load_pending(root: Path) -> tuple[list[dict], list[str]]:
    path = root / PENDING
    if not path.is_file():
        return [], []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data["entries"]
        assert isinstance(entries, list)
    except (ValueError, KeyError, AssertionError, TypeError):
        return [], [f"{PENDING}: unreadable"]
    errors = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "command", "count"}:
            errors.append(f"{PENDING}: entry must have exactly path, command, count")
        elif not str(entry["path"]).startswith(PENDING_PREFIX):
            errors.append(f"{PENDING}: only {PENDING_PREFIX} lines may be pending")
    return entries, errors


def apply_pending(
    findings: list[Finding], entries: list[dict]
) -> tuple[list[Finding], list[Finding], list[str]]:
    """Split findings into (violations, pending); entries must match exactly."""
    lines: dict[tuple[str, str], set[int]] = {}
    for f in findings:
        lines.setdefault((f.path, f.text), set()).add(f.line)
    allowed: set[tuple[str, str]] = set()
    errors: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or not str(entry.get("path", "")).startswith(
            PENDING_PREFIX
        ):
            continue
        key = (str(entry.get("path")), str(entry.get("command")))
        have = len(lines.get(key, ()))
        if have != entry.get("count"):
            errors.append(
                f"{PENDING}: {key[0]}: {key[1]!r} pending x{entry.get('count')}, but"
                f" {have} violating line(s) match; update or delete the entry"
            )
            continue
        allowed.add(key)
    violations = [f for f in findings if (f.path, f.text) not in allowed]
    pending = [f for f in findings if (f.path, f.text) in allowed]
    return violations, pending, errors


def check(root: Path) -> tuple[list[str], list[str]]:
    """Return (violations, pending notes) for the repository at root."""
    tracked = tracked_files(root)
    findings: list[Finding] = []
    for rel in tracked:
        if source_kind(rel) is None or not (root / rel).is_file():
            continue
        findings += scan_text(rel, (root / rel).read_text(encoding="utf-8"))
    entries, errors = load_pending(root)
    violations, pending, pending_errors = apply_pending(findings, entries)
    out = [
        f"{f.path}:{f.line}: {f.rule}"
        for f in sorted(set(violations), key=lambda f: (f.path, f.line, f.rule))
    ]
    out += sorted(set(errors + pending_errors))
    out += sorted(check_locks(root, tracked))
    notes = [
        f"{path}:{line}: {text}"
        for path, line, text in sorted({(f.path, f.line, f.text) for f in pending})
    ]
    return out, notes


def main(argv: list[str] | None = None) -> int:
    summary = (__doc__ or "").splitlines()[0]
    parser = argparse.ArgumentParser(description=summary)
    parser.add_argument("--root", default=".", help="repository root (default: cwd)")
    parser.add_argument(
        "--write-evidence",
        action="store_true",
        help=f"rewrite {EVIDENCE} from the current locks (after re-locking)",
    )
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if args.write_evidence:
        text = json.dumps(build_evidence(root), indent=2, sort_keys=True) + "\n"
        (root / EVIDENCE).write_text(text, encoding="utf-8")
        sys.stdout.write(f"wrote {EVIDENCE}\n")
        return 0
    violations, notes = check(root)
    for note in notes:
        sys.stdout.write(f"pending workflow install (maintainer): {note}\n")
    for violation in violations:
        sys.stdout.write(f"install violation: {violation}\n")
    if violations:
        sys.stdout.write(
            f"{len(violations)} install violation(s); see docs/DEPENDENCY-LOCKS.md\n"
        )
        return 1
    sys.stdout.write(f"install pins OK ({len(notes)} pending workflow line(s))\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
