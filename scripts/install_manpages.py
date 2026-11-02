#!/usr/bin/env python3
"""Install the shipped man pages into $DESTDIR$PREFIX/share/man/man1 (#188).

Only the pages for the console commands the package installs (pyproject
[project.scripts]: parking-report, parking-report-106, parking-report-http)
are installed; internal scripts under scripts/ have no pages. No sudo, no
network: it copies files and nothing else.

  PREFIX=/usr/local (default)   DESTDIR= (default empty, for staging)
  python scripts/install_manpages.py [--prefix P] [--destdir D] [--source DIR]

Flags override the PREFIX/DESTDIR environment variables. Opt-in diagnostics
(OFF by default): --debug or PARKING_MANPAGES_DEBUG=1 prints stage names to
stderr; logs never contain paths, arguments or environment values, and a
failure prints only the stage and the error class. Stdlib only, POSIX only.

Path safety (#207):

- PREFIX must be absolute and must not contain a ".." component, so
  $DESTDIR$PREFIX cannot climb out of the staging directory; the joined
  destination is also checked, by path normalisation (no symlink is
  followed), to lie inside DESTDIR when DESTDIR is set. A bad PREFIX fails
  at stage=destination with ValueError.
- Trusted root: DESTDIR when it is set, otherwise PREFIX. The trusted root
  and its parents are trusted as they are (they may be symlinks, e.g. /tmp on
  macOS) and are created if missing. Every directory *below* the trusted
  root (the PREFIX components under DESTDIR, then share/man/man1) must be a
  real directory: each is opened with O_NOFOLLOW relative to its parent, and
  a symlink there is refused with UnsafePathError.
- A page is never written or chmod-ed through an existing entry: an existing
  page that is a symlink (or anything but a regular file) is refused with
  UnsafePathError before any page is written. The bytes go to a new
  temporary file created with O_EXCL|O_NOFOLLOW in the man1 directory, the
  mode is set with fchmod on that file's own descriptor, and os.replace()
  swaps the directory entry. A link target or hard-linked file is never
  opened for writing.
- Not a defence against a concurrent attacker: run it only on directories
  that nobody else can write to while it runs. Someone who can write to the
  staging tree at the same time can still race it (for example by swapping
  an entry between the check and the replace; os.replace then replaces the
  swapped entry itself, it does not write through it).

Stale pages (#204): the installer owns only the pages it wrote itself. After
a successful copy it records them, with the SHA-256 of the bytes it wrote, in
a manifest next to man1, share/man/.parking-report-helper-man1.manifest (in
share/man, not man1, so mandb does not warn about a bogus page name). On the
next install a page is removed only when all of these hold:

- it is listed in the previous manifest and is no longer shipped;
- its manifest name is a plain page name (no "/", no leading ".", ends in
  ".1"); the whole manifest is ignored otherwise, so an entry such as
  "../x.1" or "/etc/x.1" can never name a file outside man1;
- the entry in man1 is a regular file (a symlink, directory or anything
  else is kept, never followed) whose bytes still match the recorded hash
  (a page someone edited or replaced since is kept).

A missing manifest (first install, or an install made before #204) owns
nothing. An unreadable or malformed manifest is ignored as a whole: nothing
is removed and one line naming only the error class goes to stderr; the
install still succeeds. Pages that are kept are disowned: the new manifest
lists exactly the pages installed this time, so they are never retried.
Unrelated pages, including ones with similar names or in other sections,
are never listed and therefore never touched. There is no dry-run mode; the
manifest is written last, so an interrupted cleanup is retried next time.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import importlib.util
import os
import posixpath
import re
import stat
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import NamedTuple

HERE = Path(__file__).resolve().parent
LOG_NAME = "install_manpages"
MAN_SUBDIR = ("share", "man", "man1")
PAGE_MODE = 0o644
MANIFEST_NAME = ".parking-report-helper-man1.manifest"
MANIFEST_HEADER = "# parking-report-helper man1 pages installed by install_manpages.py"
MANIFEST_MAX_BYTES = 64 * 1024
PAGE_MAX_BYTES = 1024 * 1024
PAGE_NAME = r"[A-Za-z0-9][A-Za-z0-9._+-]*\.1"
MANIFEST_LINE = re.compile(r"([0-9a-f]{64})  (" + PAGE_NAME + r")")


class UnsafePathError(OSError):
    """A symlink or non-regular entry where the installer would write."""


class ManifestError(ValueError):
    """The previous manifest is unreadable or not in the expected format."""


class Result(NamedTuple):
    installed: list[str]
    removed: list[str]
    kept: list[tuple[str, str]]
    manifest_error: str | None


def _generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "gen_manpages", HERE / "gen_manpages.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError("generator not found")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def plan(prefix: str, destdir: str) -> tuple[Path, tuple[str, ...]]:
    """Return (trusted root, directory components below it) for the pages.

    The trusted root is DESTDIR when set, else PREFIX. The components are
    plain names (no "", "." or ".."), ending in share/man/man1.
    """
    if not prefix.startswith("/"):
        raise ValueError("PREFIX must be absolute")
    parts = prefix.split("/")
    if ".." in parts:
        raise ValueError("PREFIX must not contain '..'")
    names = tuple(p for p in parts if p not in ("", "."))
    if not destdir:
        return Path(posixpath.normpath(prefix)), MAN_SUBDIR
    root = Path(destdir)
    below = (*names, *MAN_SUBDIR)
    joined = os.path.normpath(os.path.abspath(os.path.join(destdir, *below)))
    stage = os.path.normpath(os.path.abspath(destdir))
    if os.path.commonpath([stage, joined]) != stage:
        raise ValueError("destination escapes DESTDIR")
    return root, below


def _open_dir(name: str, parent_fd: int) -> int:
    """Open (creating if needed) a real directory below parent_fd."""
    try:
        os.mkdir(name, dir_fd=parent_fd)
    except FileExistsError:
        pass
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise UnsafePathError("not a real directory") from None
        raise


def _check_entry(name: str, dir_fd: int) -> None:
    """Refuse an existing page entry that is not a plain regular file."""
    try:
        st = os.lstat(name, dir_fd=dir_fd)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(st.st_mode):
        raise UnsafePathError("existing page is not a regular file")


def _replace_page(data: bytes, name: str, dir_fd: int) -> None:
    tmp = f".{name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(tmp, flags, 0o600, dir_fd=dir_fd)
    try:
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
            os.fchmod(fd, PAGE_MODE)
            os.fsync(fd)
        finally:
            os.close(fd)
        _check_entry(name, dir_fd)
        os.replace(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        raise


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def format_manifest(data: Mapping[str, bytes]) -> bytes:
    """Manifest bytes: a fixed header, then "<sha256>  <page>" sorted by page."""
    lines = [MANIFEST_HEADER]
    lines += [f"{_sha256(data[page])}  {page}" for page in sorted(data)]
    return ("\n".join(lines) + "\n").encode("ascii")


def parse_manifest(raw: bytes) -> dict[str, str]:
    """Page name -> recorded SHA-256. Any deviation rejects the whole file."""
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        raise ManifestError("not ASCII") from None
    if not text.endswith("\n"):
        raise ManifestError("truncated")
    lines = text[:-1].split("\n")
    if lines[0] != MANIFEST_HEADER:
        raise ManifestError("unknown header")
    owned: dict[str, str] = {}
    for line in lines[1:]:
        match = MANIFEST_LINE.fullmatch(line)
        if match is None or match.group(2) in owned:
            raise ManifestError("bad entry")
        owned[match.group(2)] = match.group(1)
    return owned


def _read_regular(name: str, dir_fd: int, limit: int) -> bytes | None:
    """Bytes of a regular file entry (never followed), None if it is not one.

    Raises ValueError when the file is larger than limit.
    """
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(name, flags, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return None
        raise
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        chunks = []
        size = 0
        while chunk := os.read(fd, 65536):
            chunks.append(chunk)
            size += len(chunk)
            if size > limit:
                raise ValueError("file too large")
        return b"".join(chunks)
    finally:
        os.close(fd)


def read_manifest(dir_fd: int) -> dict[str, str]:
    """The previous manifest; {} when there is none. Raises ManifestError."""
    try:
        raw = _read_regular(MANIFEST_NAME, dir_fd, MANIFEST_MAX_BYTES)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise ManifestError("unreadable") from None
    if raw is None:
        raise ManifestError("not a regular file")
    return parse_manifest(raw)


def remove_stale(
    owned: Mapping[str, str], shipped: Sequence[str], dir_fd: int
) -> tuple[list[str], list[tuple[str, str]]]:
    """Remove owned pages that are no longer shipped and still unchanged."""
    removed: list[str] = []
    kept: list[tuple[str, str]] = []
    for page in sorted(set(owned) - set(shipped)):
        try:
            before = os.lstat(page, dir_fd=dir_fd)
        except FileNotFoundError:
            continue
        data = None
        if stat.S_ISREG(before.st_mode):
            try:
                data = _read_regular(page, dir_fd, PAGE_MAX_BYTES)
            except FileNotFoundError:
                continue
            except ValueError:
                kept.append((page, "changed since install"))
                continue
        if data is None:
            kept.append((page, "not a regular file"))
            continue
        if _sha256(data) != owned[page]:
            kept.append((page, "changed since install"))
            continue
        os.unlink(page, dir_fd=dir_fd)
        removed.append(page)
    return removed, kept


def install(
    source: Path,
    root: Path,
    below: Sequence[str],
    pages: Sequence[str],
    at: Callable[[str], None] = lambda stage: None,
) -> Result:
    missing = [p for p in pages if not (source / p).is_file()]
    if missing:
        raise FileNotFoundError("a shipped page is missing")
    data = {page: (source / page).read_bytes() for page in pages}
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    man_fd = -1
    try:
        for index, name in enumerate(below):
            child = _open_dir(name, fd)
            if index == len(below) - 1:
                man_fd = fd  # share/man, where the manifest lives
            else:
                os.close(fd)
            fd = child
        _check_entry(MANIFEST_NAME, man_fd)
        for page in pages:
            _check_entry(page, fd)
        manifest_error = None
        try:
            owned = read_manifest(man_fd)
        except ManifestError as exc:
            owned, manifest_error = {}, type(exc).__name__
        for page in pages:
            _replace_page(data[page], page, fd)
        at("cleanup")
        removed, kept = remove_stale(owned, pages, fd)
        at("manifest")
        _replace_page(format_manifest(data), MANIFEST_NAME, man_fd)
    finally:
        os.close(fd)
        if man_fd >= 0:
            os.close(man_fd)
    return Result(list(pages), removed, kept, manifest_error)


def main(argv: Sequence[str] | None = None) -> int:
    gen = _generator()
    parser = argparse.ArgumentParser(
        prog="install_manpages.py", description="Install the shipped man pages."
    )
    parser.add_argument("--prefix", default=os.environ.get("PREFIX", "/usr/local"))
    parser.add_argument("--destdir", default=os.environ.get("DESTDIR", ""))
    parser.add_argument("--source", type=Path, default=gen.DEFAULT_OUTPUT)
    parser.add_argument(
        "--debug",
        action="store_true",
        help=f"stage diagnostics (or {gen.DEBUG_ENV}=1)",
    )
    args = parser.parse_args(argv)
    log = gen.Log(gen.debug_enabled(args.debug, os.environ), LOG_NAME)
    try:
        log.at("inventory")
        pages = [f"{name}.1" for name in gen.shipped_commands()]
        log.at("destination")
        root, below = plan(args.prefix, args.destdir)
        log.at("copy")
        result = install(args.source, root, below, pages, log.at)
    except Exception as exc:  # noqa: BLE001 - stage and class only, no text
        log.failed(exc)
        return 1
    log.at("done")
    if result.manifest_error:
        sys.stderr.write(
            f"{LOG_NAME}: previous manifest ignored "
            f"error={result.manifest_error}; no stale page removed\n"
        )
    for page in result.installed:
        print(f"installed {page}")
    for page in result.removed:
        print(f"removed stale {page}")
    for page, reason in result.kept:
        print(f"kept stale {page} ({reason})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
