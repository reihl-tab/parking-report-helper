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
"""

from __future__ import annotations

import argparse
import errno
import importlib.util
import os
import posixpath
import stat
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

HERE = Path(__file__).resolve().parent
LOG_NAME = "install_manpages"
MAN_SUBDIR = ("share", "man", "man1")
PAGE_MODE = 0o644


class UnsafePathError(OSError):
    """A symlink or non-regular entry where the installer would write."""


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


def install(
    source: Path, root: Path, below: Sequence[str], pages: Sequence[str]
) -> list[str]:
    missing = [p for p in pages if not (source / p).is_file()]
    if missing:
        raise FileNotFoundError("a shipped page is missing")
    data = {page: (source / page).read_bytes() for page in pages}
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in below:
            child = _open_dir(name, fd)
            os.close(fd)
            fd = child
        for page in pages:
            _check_entry(page, fd)
        for page in pages:
            _replace_page(data[page], page, fd)
    finally:
        os.close(fd)
    return list(pages)


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
        installed = install(args.source, root, below, pages)
    except Exception as exc:  # noqa: BLE001 - stage and class only, no text
        log.failed(exc)
        return 1
    log.at("done")
    for page in installed:
        print(f"installed {page}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
