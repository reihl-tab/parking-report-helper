"""Self-contained HTML statistics report, generated locally (#210).

The interface text comes from the report catalog (report_i18n, td423):
``build_report(summary, lang=...)`` renders it in any catalog language, and
the page's ``lang``, ``dir`` and CSS direction come from that language's
block. The default language renders the same page as before the catalog.
City data (category, street, address and status names) is never translated.
"""

from __future__ import annotations

import html

from . import report_i18n
from .report_i18n import text
from .stats_charts import (
    category_chart,
    closure_by_category_chart,
    closure_chart,
    hotspot_chart,
    monthly_chart,
    neighborhood_chart,
    status_chart,
    street_chart,
    weekly_chart,
)

_CSS = """
body { font-family: sans-serif; direction: __DIRECTION__; margin: 2em auto;
       max-width: 960px; color: #1a202c; }
h1 { font-size: 1.5em; }
h2 { font-size: 1.15em; margin-top: 1.8em; }
table { border-collapse: collapse; }
td, th { border: 1px solid #cbd5e0; padding: 4px 10px; font-size: 0.9em; }
.note { color: #4a5568; font-size: 0.85em; }
svg { display: block; margin: 0.5em 0; overflow: visible; }
"""


def _esc(text: object) -> str:
    return html.escape(str(text))


def _kv_table(rows: list[tuple[str, object]]) -> str:
    body = "".join(
        f"<tr><th>{_esc(key)}</th><td>{_esc(value)}</td></tr>" for key, value in rows
    )
    return f"<table>{body}</table>"


def _shown(series: dict, labels: dict[str, str]) -> dict:
    """Show stats.py's own labels in the page language; city names as-is."""
    return {labels.get(name, name): value for name, value in series.items()}


def build_report(summary: dict, lang: str | None = None) -> str:
    """Render the full report.html for one summary document.

    ``lang`` is a catalog language code; None means the catalog default.
    """
    language = report_i18n.language(lang)
    code = language.code
    labels = report_i18n.display_labels(code)
    closure = summary["closure"]
    date_range = summary["date_range"]
    overview = _kv_table(
        [
            (text("overview.total", code), summary["total_incidents"]),
            (text("overview.duplicates", code), summary.get("duplicates_skipped", 0)),
            (
                text("overview.date_range", code),
                text(
                    "overview.date_range_value",
                    code,
                    first=date_range["first"],
                    last=date_range["last"],
                ),
            ),
            (text("overview.closed", code), closure["closed_count"]),
            (text("overview.median_days", code), closure["median_days"]),
            (text("overview.p75_days", code), closure["p75_days"]),
            (text("overview.max_days", code), closure["max_days"]),
        ]
    )
    medians = closure["median_days_by_category"]
    neighborhood = summary.get("by_neighborhood")
    neighborhood_section = ""
    if neighborhood:
        neighborhood_section = (
            f"<h2>{_esc(text('chart.by_neighborhood', code))}</h2>"
            + neighborhood_chart(_shown(neighborhood, labels), lang=code)
        )
    inputs = ", ".join(_esc(path) for path in summary.get("inputs", []))
    notes = ""
    if language.machine_draft:
        notes += f'<p class="note">{_esc(text("report.machine_draft_note", code))}</p>'
    if not language.default:
        notes += f'<p class="note">{_esc(text("report.city_data_note", code))}</p>'
    title = _esc(text("report.title", code))
    # The inputs are escaped above; the catalog text around them is escaped
    # before the substitution so a file name is never escaped twice.
    generated = _esc(text("report.generated", code)).replace("{inputs}", inputs)

    def h2(key: str) -> str:
        return f"<h2>{_esc(text(key, code))}</h2>"

    css = _CSS.replace("__DIRECTION__", language.direction)
    return f"""<!DOCTYPE html>
<html lang="{code}" dir="{language.direction}">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<h1>{title}</h1>
<p class="note">{generated}</p>{notes}
{h2("section.overview")}
{overview}
{h2("chart.by_month")}
{monthly_chart(summary["by_month"], lang=code)}
{h2("section.by_week")}
{weekly_chart(summary["by_week"], lang=code)}
{h2("chart.by_category")}
{category_chart(_shown(summary["by_category"], labels), lang=code)}
{neighborhood_section}
{h2("chart.top_streets")}
{street_chart(_shown(summary["by_street"], labels), lang=code)}
{h2("section.hotspots")}
{hotspot_chart(summary["hotspots"], lang=code)}
{h2("section.closure")}
{closure_chart(_shown(closure["buckets"], labels), lang=code)}
{h2("chart.closure_by_category")}
{closure_by_category_chart(_shown(medians, labels), lang=code)}
{h2("chart.by_status")}
{status_chart(_shown(summary["by_status"], labels), lang=code)}
</body>
</html>
"""
