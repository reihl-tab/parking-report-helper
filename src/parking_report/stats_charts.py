"""Dependency-free SVG charts for the local 106 statistics report (#210).

Pure-python SVG keeps the package dependency-free (CI hash-locks every
dependency) and fully testable: charts are strings, assertions are string
and structure checks. Hebrew labels are written RTL; numbers stay LTR.
"""

from __future__ import annotations

import html

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
) -> str:
    """One SVG bar chart: Hebrew-friendly horizontal bars, values at the end."""
    width = label_width + 400 + VALUE_WIDTH + PADDING * 2
    height = PADDING * 2 + 30 + len(series) * (BAR_HEIGHT + BAR_GAP)
    if not series:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="60" dir="rtl"><text x="50%" y="35" text-anchor="middle" '
            f'font-size="14">אין נתונים</text></svg>'
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
        bar_len = max(2, round(400 * value / maximum))
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


def monthly_chart(by_month: dict[str, int]) -> str:
    return horizontal_bars(
        list(by_month.items()), title="דיווחים לפי חודש", label_width=90
    )


def category_chart(by_category: dict[str, int]) -> str:
    return horizontal_bars(
        list(by_category.items()), title="דיווחים לפי קטגוריה", color=COLORS[1]
    )


def street_chart(by_street: dict[str, int]) -> str:
    return horizontal_bars(
        list(by_street.items()), title="הרחובות המדווחים ביותר", color=COLORS[3]
    )


def closure_chart(buckets: dict[str, int]) -> str:
    return horizontal_bars(
        list(buckets.items()),
        title="זמן סגירת פניות (חלוקה לדליים)",
        color=COLORS[4],
        label_width=150,
    )


def weekly_chart(by_week: dict[str, int]) -> str:
    return horizontal_bars(
        list(by_week.items()), title="דיווחים לפי שבוע (12 אחרונים)", label_width=90
    )


def hotspot_chart(hotspots: dict[str, int]) -> str:
    return horizontal_bars(
        list(hotspots.items()),
        title="נקודות חמות: הכתובות המדווחות ביותר",
        color=COLORS[2],
        label_width=190,
    )


def neighborhood_chart(by_neighborhood: dict[str, int]) -> str:
    return horizontal_bars(
        list(by_neighborhood.items()),
        title="דיווחים לפי שכונה",
        color=COLORS[5],
        label_width=150,
    )


def status_chart(by_status: dict[str, int]) -> str:
    return horizontal_bars(
        list(by_status.items()), title="סטטוס פניות", color=COLORS[3], label_width=130
    )


def closure_by_category_chart(medians: dict[str, float]) -> str:
    return horizontal_bars(
        list(medians.items()),
        title="חציון ימי סגירה לפי קטגוריה",
        color=COLORS[4],
    )
