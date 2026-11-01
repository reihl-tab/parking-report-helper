#!/usr/bin/env python3
"""Install the shipped man pages into $DESTDIR$PREFIX/share/man/man1 (#188).

Only the pages for the console commands the package installs (pyproject
[project.scripts]: parking-report, parking-report-106, parking-report-http)
are installed; internal scripts under scripts/ have no pages. No sudo, no
network: it copies files and nothing else.

  PREFIX=/usr/local (default)   DESTDIR= (default empty, for staging)
  python scripts/install_manpages.py [--prefix P] [--destdir D] [--source DIR]

Flags override the PREFIX/DESTDIR environment variables. PREFIX must be
absolute. Opt-in diagnostics (OFF by default): --debug or
PARKING_MANPAGES_DEBUG=1 prints stage names to stderr; logs never contain
paths, arguments or environment values, and a failure prints only the stage
and the error class. Stdlib only.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

HERE = Path(__file__).resolve().parent
LOG_NAME = "install_manpages"


def _generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "gen_manpages", HERE / "gen_manpages.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError("generator not found")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def destination(prefix: str, destdir: str) -> Path:
    if not prefix.startswith("/"):
        raise ValueError("PREFIX must be absolute")
    return Path(destdir + prefix) / "share" / "man" / "man1"


def install(source: Path, target: Path, pages: Sequence[str]) -> list[str]:
    missing = [p for p in pages if not (source / p).is_file()]
    if missing:
        raise FileNotFoundError("a shipped page is missing")
    target.mkdir(parents=True, exist_ok=True)
    for page in pages:
        shutil.copyfile(source / page, target / page)
        os.chmod(target / page, 0o644)
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
        target = destination(args.prefix, args.destdir)
        log.at("copy")
        installed = install(args.source, target, pages)
    except Exception as exc:  # noqa: BLE001 - stage and class only, no text
        log.failed(exc)
        return 1
    log.at("done")
    for page in installed:
        print(f"installed {page}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
