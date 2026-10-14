"""Builds the SVG geometry for the student portal's progress-over-time chart.

Computed server-side (positions, colors, labels all resolved here) rather
than shipped as data for client-side JS to render - this app has no JS
framework or build step, so a plain inline SVG the template loops over is the
natural fit. See the dataviz skill for the rules this follows: topics are the
"series" (categorical color, fixed order), the shared y-axis is the ordinal
mastery tier (Struggling/Practicing/Mastered) rather than a number, and each
marker carries a native <title> tooltip as the hover layer.
"""

from dataclasses import dataclass, field
from datetime import datetime

from student_adaptive_mcp.progress import MASTERY_TIERS, ProgressEntry, mastery_ordinal

# Dark-mode categorical slots, validated (dataviz skill's validate_palette.js)
# against this app's actual panel surface (#12151c): all checks pass. Fixed
# order - never reassigned per render, never cycled past this list.
SERIES_COLORS = [
    "#3987e5",  # blue
    "#d95926",  # orange
    "#199e70",  # aqua
    "#c98500",  # yellow
    "#d55181",  # magenta
    "#008300",  # green
    "#9085e9",  # violet
    "#e66767",  # red
]
MAX_SERIES = len(SERIES_COLORS)

CHART_W, CHART_H = 680, 180
MARGIN = {"top": 16, "right": 108, "bottom": 30, "left": 96}


@dataclass
class ChartPoint:
    x: float
    y: float
    date_label: str
    mastery_level: str
    notes: str


@dataclass
class ChartSeries:
    topic: str
    color: str
    points: list[ChartPoint]
    polyline: str
    label_y: float


@dataclass
class ProgressChart:
    series: list[ChartSeries] = field(default_factory=list)
    overflow_count: int = 0
    tier_labels: list[tuple[str, float]] = field(default_factory=list)
    start_date_label: str = ""
    end_date_label: str = ""
    width: int = CHART_W + MARGIN["left"] + MARGIN["right"]
    height: int = CHART_H + MARGIN["top"] + MARGIN["bottom"]


def _parse(logged_at: str) -> datetime:
    return datetime.fromisoformat(logged_at.replace("Z", "+00:00"))


def _format_date(ts: datetime) -> str:
    # "%-d" (no leading zero) isn't portable - Windows strftime only knows
    # "%#d" for that, Linux/macOS only know "%-d". Build it by hand instead.
    return f"{ts.strftime('%b')} {ts.day}"


def _tier_y(tier: int) -> float:
    # tier 2 (Mastered) at the top, tier 0 (Struggling) at the bottom.
    return MARGIN["top"] + CHART_H * (1 - tier / 2)


def build_progress_chart(by_topic: dict[str, list[ProgressEntry]]) -> ProgressChart | None:
    all_entries = [e for entries in by_topic.values() for e in entries]
    if not all_entries:
        return None

    timestamps = [_parse(e.logged_at) for e in all_entries]
    t_min, t_max = min(timestamps), max(timestamps)
    span = (t_max - t_min).total_seconds()

    def x_for(ts: datetime, index: int, count: int) -> float:
        if span > 0:
            frac = (ts - t_min).total_seconds() / span
        else:
            # All updates logged at (near) the same instant - fall back to
            # spreading by order rather than dividing by zero.
            frac = index / (count - 1) if count > 1 else 0.5
        return MARGIN["left"] + frac * CHART_W

    topics = list(by_topic.keys())
    overflow_count = max(0, len(topics) - MAX_SERIES)
    series: list[ChartSeries] = []

    for i, topic in enumerate(topics[:MAX_SERIES]):
        entries = by_topic[topic]
        points = []
        for j, entry in enumerate(entries):
            ts = _parse(entry.logged_at)
            points.append(
                ChartPoint(
                    x=round(x_for(ts, j, len(entries)), 1),
                    y=round(_tier_y(mastery_ordinal(entry.mastery_level)), 1),
                    date_label=_format_date(ts),
                    mastery_level=entry.mastery_level,
                    notes=entry.notes,
                )
            )
        polyline = " ".join(f"{p.x},{p.y}" for p in points)
        series.append(
            ChartSeries(topic=topic, color=SERIES_COLORS[i], points=points, polyline=polyline, label_y=points[-1].y)
        )

    return ProgressChart(
        series=series,
        overflow_count=overflow_count,
        tier_labels=[(label, _tier_y(tier)) for tier, label in enumerate(MASTERY_TIERS)],
        start_date_label=_format_date(t_min),
        end_date_label=_format_date(t_max) if span > 0 else "",
    )
