"""Interface text for report.html, read from one catalog (td423/td424).

``configs/report_strings.json`` is the only place that names a report
language. Each language is one block in ``languages``: its code, its own
name (shown wherever a language is offered), its text direction, whether it
is the default, and whether it is a machine draft (with a ``status`` in
words). ``strings`` maps each key to one value per catalog language.

Everything else derives the language list from here: ``p106-stats --lang``
accepts exactly the catalog codes, the report takes its ``lang``/``dir``
from the block, and the tests iterate the catalog. Adding a language is one
new block plus one value per key; no code changes.

Only the report's own interface text lives here. City data (category,
street, address and status names) is shown as the city wrote it, and
summary.json and the CSV tables are never localized.
"""

from __future__ import annotations

import argparse
import functools
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import stats

CATALOG_PATH = Path(__file__).resolve().parent / "configs" / "report_strings.json"
DIRECTIONS = ("rtl", "ltr")
_LANGUAGE_FIELDS = {"code", "name", "dir", "default", "machine_draft", "status"}
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Report labels that stats.py writes into summary.json in the default
# language. The report shows them in the page language; summary.json and the
# CSV tables keep the stats.py values.
_CLOSURE_KEYS = (
    "closure.under_1h",
    "closure.1h_to_8h",
    "closure.8h_to_1d",
    "closure.1d_to_7d",
    "closure.over_7d",
)
_PLACEHOLDER_KEYS = {
    stats.NO_STREET: "label.no_street",
    stats.UNKNOWN: "label.unknown",
    stats.OTHER_NEIGHBORHOOD: "label.other_neighborhood",
}
if len(_CLOSURE_KEYS) != len(stats.CLOSURE_BUCKETS):  # pragma: no cover
    raise RuntimeError("closure bucket keys and stats.CLOSURE_BUCKETS differ")

REQUIRED_KEYS = (
    "report.title",
    "report.generated",
    "report.machine_draft_note",
    "report.city_data_note",
    "section.overview",
    "overview.total",
    "overview.duplicates",
    "overview.date_range",
    "overview.date_range_value",
    "overview.closed",
    "overview.median_days",
    "overview.p75_days",
    "overview.max_days",
    "chart.by_month",
    "section.by_week",
    "chart.by_week",
    "chart.by_category",
    "chart.by_neighborhood",
    "chart.top_streets",
    "section.hotspots",
    "chart.hotspots",
    "section.closure",
    "chart.closure",
    "chart.closure_by_category",
    "chart.by_status",
    "chart.no_data",
    *_CLOSURE_KEYS,
    *_PLACEHOLDER_KEYS.values(),
)


class CatalogError(ValueError):
    """The report catalog is missing, unreadable or inconsistent."""


@dataclass(frozen=True)
class Language:
    code: str
    name: str
    direction: str
    default: bool
    machine_draft: bool
    status: str | None


def placeholders(value: str) -> set[str]:
    """The ``{name}`` placeholders in one catalog value."""
    return set(_PLACEHOLDER.findall(value))


def _identity_problems(where: str, block: dict) -> list[str]:
    problems = []
    unknown = sorted(set(block) - _LANGUAGE_FIELDS)
    if unknown:
        problems.append(f"{where} has unknown fields {unknown}")
    code = block.get("code")
    if not isinstance(code, str) or not code.strip():
        problems.append(f"{where}.code must be a non-empty string")
    name = block.get("name")
    if not isinstance(name, str) or not name.strip():
        problems.append(f"{where}.name must be the language's own name")
    return problems


def _flag_problems(where: str, block: dict) -> list[str]:
    problems = []
    if block.get("dir") not in DIRECTIONS:
        problems.append(f"{where}.dir must be one of {list(DIRECTIONS)}")
    if not isinstance(block.get("default"), bool):
        problems.append(f"{where}.default must be true or false")
    draft = block.get("machine_draft")
    status = block.get("status")
    if not isinstance(draft, bool):
        problems.append(f"{where}.machine_draft must be true or false")
    elif draft and not (isinstance(status, str) and status.strip()):
        problems.append(f"{where} is a machine draft and needs a status")
    elif not draft and status is not None:
        problems.append(f"{where} is not a machine draft but has a status")
    return problems


def _language_problems(blocks: object) -> list[str]:
    if not isinstance(blocks, list) or not blocks:
        return ["languages must be a non-empty list of language blocks"]
    if not all(isinstance(block, dict) for block in blocks):
        return ["every language block must be an object"]
    problems: list[str] = []
    for index, block in enumerate(blocks):
        where = f"languages[{index}]"
        problems += _identity_problems(where, block) + _flag_problems(where, block)
    codes = [block["code"] for block in blocks if isinstance(block.get("code"), str)]
    if len(set(codes)) != len(codes):
        problems.append("language codes must be unique")
    if sum(1 for block in blocks if block.get("default") is True) != 1:
        problems.append("exactly one language must be the default")
    return problems


def _value_problems(
    key: str, values: dict, codes: list[str], default: str
) -> list[str]:
    problems = []
    stray = sorted(set(values) - set(codes))
    if stray:
        problems.append(f"{key} names languages outside the catalog: {stray}")
    expected = None
    if isinstance(values.get(default), str):
        expected = placeholders(values[default])
    for code in codes:
        value = values.get(code)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{key} has no {code} text")
        elif expected is not None and placeholders(value) != expected:
            problems.append(f"{key} {code} placeholders differ from the default")
    return problems


def catalog_problems(data: object) -> list[str]:
    """Every inconsistency in a catalog document; an empty list means valid."""
    if not isinstance(data, dict) or set(data) != {"languages", "strings"}:
        return ["the catalog must be one object with languages and strings"]
    problems = _language_problems(data["languages"])
    if problems:
        return problems
    blocks = data["languages"]
    codes = [block["code"] for block in blocks]
    default = next(block["code"] for block in blocks if block["default"])
    strings = data["strings"]
    if not isinstance(strings, dict):
        return ["strings must be an object mapping keys to values per language"]
    for key in REQUIRED_KEYS:
        if key not in strings:
            problems.append(f"missing key {key}")
    for key, values in strings.items():
        if not isinstance(values, dict):
            problems.append(f"{key} must map language codes to text")
        else:
            problems += _value_problems(key, values, codes, default)
    return problems


@functools.lru_cache(maxsize=8)
def _load(path: Path) -> tuple[tuple[Language, ...], dict[str, dict[str, str]]]:
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise CatalogError(f"report catalog unreadable: {type(exc).__name__}")
    problems = catalog_problems(data)
    if problems:
        raise CatalogError("report catalog invalid: " + "; ".join(problems))
    languages = tuple(
        Language(
            code=block["code"],
            name=block["name"],
            direction=block["dir"],
            default=block["default"],
            machine_draft=block["machine_draft"],
            status=block.get("status"),
        )
        for block in data["languages"]
    )
    return languages, data["strings"]


def _catalog() -> tuple[tuple[Language, ...], dict[str, dict[str, str]]]:
    return _load(CATALOG_PATH)


def languages() -> tuple[Language, ...]:
    """The catalog's languages, in catalog order."""
    return _catalog()[0]


def default_language() -> Language:
    return next(language for language in languages() if language.default)


def language(code: str | None = None) -> Language:
    """The catalog block for ``code``; None means the default language."""
    if code is None:
        return default_language()
    for candidate in languages():
        if candidate.code == code:
            return candidate
    raise ValueError(f"unknown report language {code!r}")


def available() -> str:
    """The catalog's languages for messages: ``code (own name)``, in order."""
    return ", ".join(f"{item.code} ({item.name})" for item in languages())


def language_code(value: str) -> str:
    """argparse type for --lang: exactly the catalog's codes."""
    try:
        return language(value).code
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"unknown report language {value!r}; the catalog has: {available()}"
        ) from None


def text(key: str, lang: str | None = None, **params: object) -> str:
    """One interface string in ``lang`` (default language when None)."""
    values = _catalog()[1][key]
    value = values[language(lang).code]
    return value.format(**params) if params else value


def display_labels(lang: str | None = None) -> dict[str, str]:
    """stats.py's own summary labels mapped to their text in ``lang``."""
    keys = dict(_PLACEHOLDER_KEYS)
    for (name, _, _), key in zip(stats.CLOSURE_BUCKETS, _CLOSURE_KEYS):
        keys[name] = key
    return {label: text(key, lang) for label, key in keys.items()}
