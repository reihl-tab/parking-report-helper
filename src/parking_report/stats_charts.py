"""Dependency-free SVG charts for the local 106 statistics report (#210).

Pure-python SVG keeps the package dependency-free (CI hash-locks every
dependency) and fully testable: charts are strings, assertions are string
and structure checks. Hebrew labels are written RTL; numbers stay LTR.
Titles and the empty-chart text come from the report catalog
(report_i18n) in the page language; the bar labels are the city's data
and stay Hebrew, so every chart keeps its RTL layout.
"""

from __future__ import annotations

import html

from .report_i18n import text

BAR_HEIGHT = 22
BAR_GAP = 6
LABEL_WIDTH = 220
VALUE_WIDTH = 48
PADDING = 12
COLORS = ("#2b6cb0", "#2f855a", "#b7791f", "#c53030", "#6b46c1", "#2c7a7b")


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def horizontal_bars(
    series: list[tuple[str, int | float]],
    *,
    title: str,
    color: str = COLORS[0],
    label_width: int = LABEL_WIDTH,
    lang: str | None = None,
) -> str:
    """One SVG bar chart: Hebrew-friendly horizontal bars, values at the end."""
    width = label_width + 400 + VALUE_WIDTH + PADDING * 2
    height = PADDING * 2 + 30 + len(series) * (BAR_HEIGHT + BAR_GAP)
    if not series:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="60" dir="rtl"><text x="50%" y="35" text-anchor="middle" '
            f'font-size="14">{_esc(text("chart.no_data", lang))}</text></svg>'
        )
    maximum = max(value for _, value in series) or 1
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" dir="rtl" font-family="sans-serif">',
        f'<text x="{width - PADDING}" y="{PADDING + 16}" text-anchor="end" '
        f'font-size="16" font-weight="bold">{_esc(title)}</text>',
    ]
    y = PADDING + 30
    for label, value in series:
        # Zero counts render as no bar at all, not a misleading sliver (#226).
        bar_len = 0 if value == 0 else max(2, round(400 * value / maximum))
        parts.append(
            f'<text x="{width - PADDING}" y="{y + 15}" text-anchor="end" '
            f'font-size="13">{_esc(label)}</text>'
            f'<rect x="{width - PADDING - label_width - bar_len}" y="{y}" '
            f'width="{bar_len}" height="{BAR_HEIGHT}" fill="{color}" rx="2"/>'
            f'<text x="{PADDING + VALUE_WIDTH}" y="{y + 15}" text-anchor="end" '
            f'font-size="13" dir="ltr">{_esc(value)}</text>'
        )
        y += BAR_HEIGHT + BAR_GAP
    parts.append("</svg>")
    return "".join(parts)


def monthly_chart(by_month: dict[str, int], lang: str | None = None) -> str:
    return horizontal_bars(
        list(by_month.items()),
        title=text("chart.by_month", lang),
        label_width=90,
        lang=lang,
    )


def category_chart(by_category: dict[str, int], lang: str | None = None) -> str:
    return horizontal_bars(
        list(by_category.items()),
        title=text("chart.by_category", lang),
        color=COLORS[1],
        lang=lang,
    )


def street_chart(by_street: dict[str, int], lang: str | None = None) -> str:
    return horizontal_bars(
        list(by_street.items()),
        title=text("chart.top_streets", lang),
        color=COLORS[3],
        lang=lang,
    )


def closure_chart(buckets: dict[str, int], lang: str | None = None) -> str:
    return horizontal_bars(
        list(buckets.items()),
        title=text("chart.closure", lang),
        color=COLORS[4],
        label_width=150,
        lang=lang,
    )


def weekly_chart(by_week: dict[str, int], lang: str | None = None) -> str:
    return horizontal_bars(
        list(by_week.items()),
        title=text("chart.by_week", lang),
        label_width=90,
        lang=lang,
    )


def hotspot_chart(hotspots: dict[str, int], lang: str | None = None) -> str:
    return horizontal_bars(
        list(hotspots.items()),
        title=text("chart.hotspots", lang),
        color=COLORS[2],
        label_width=190,
        lang=lang,
    )


def neighborhood_chart(by_neighborhood: dict[str, int], lang: str | None = None) -> str:
    return horizontal_bars(
        list(by_neighborhood.items()),
        title=text("chart.by_neighborhood", lang),
        color=COLORS[5],
        label_width=150,
        lang=lang,
    )


def status_chart(by_status: dict[str, int], lang: str | None = None) -> str:
    return horizontal_bars(
        list(by_status.items()),
        title=text("chart.by_status", lang),
        color=COLORS[3],
        label_width=130,
        lang=lang,
    )


def closure_by_category_chart(
    medians: dict[str, float], lang: str | None = None
) -> str:
    return horizontal_bars(
        list(medians.items()),
        title=text("chart.closure_by_category", lang),
        color=COLORS[4],
        lang=lang,
    )
