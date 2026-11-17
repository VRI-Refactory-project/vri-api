"""The institution usage report as a PDF (V2-029 export).

Only lays out figures: everything shown comes from usage.institution_usage(), so the PDF always matches the
Analytics page for the same filters.
"""

from io import BytesIO
from xml.sax.saxutils import escape

from django.utils import timezone
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

BRAND = colors.HexColor("#3b2579")
BAR = colors.HexColor("#4f46e5")
CONTENT_WIDTH = A4[0] - 2 * 40
DAILY_UP_TO = 31  # longer periods are charted per 7 days


def _day(value):
    return value.strftime("%d %b %Y").lstrip("0")


def _minutes(value):
    """ "2h 05m", or "40 min" under an hour (as on the Analytics page)."""
    if value < 60:
        return f"{value} min"
    return f"{value // 60}h {value % 60:02d}m"


def _change(now, before):
    if before == 0:
        return "no change" if now == 0 else "none in the previous period"
    change = round((now - before) * 100 / before)
    return f"{change:+d}% vs the previous period"


def _table(rows, widths=(0.65, 0.35)):
    table = Table(rows, colWidths=[CONTENT_WIDTH * w for w in widths])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), BRAND),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
                ("PADDING", (0, 0), (-1, -1), 6),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ]
        )
    )
    return table


def _bars(over_time):
    """(labels, counts) for the chart: per day, or per 7 days for long periods."""
    if len(over_time) <= DAILY_UP_TO:
        return [row["date"].strftime("%d %b").lstrip("0") for row in over_time], [
            row["count"] for row in over_time
        ]
    labels, counts = [], []
    for start in range(0, len(over_time), 7):
        # A plain name in the slice keeps ruff and flake8 agreeing on its spacing.
        week_end = start + 7
        week = over_time[start:week_end]
        labels.append(week[0]["date"].strftime("%d %b").lstrip("0"))
        counts.append(sum(row["count"] for row in week))
    return labels, counts


def _chart(over_time):
    labels, counts = _bars(over_time)
    drawing = Drawing(CONTENT_WIDTH, 170)
    chart = VerticalBarChart()
    chart.x, chart.y, chart.width, chart.height = 30, 30, CONTENT_WIDTH - 40, 125
    chart.data = [counts]
    chart.bars[0].fillColor = BAR
    chart.bars[0].strokeColor = None
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = max(max(counts), 4)
    chart.valueAxis.valueStep = max(1, round(chart.valueAxis.valueMax / 4))
    chart.valueAxis.labels.fontSize = 7
    # Label about 10 bars so the dates do not overlap.
    every = max(1, -(-len(labels) // 10))
    chart.categoryAxis.categoryNames = [
        label if i % every == 0 else "" for i, label in enumerate(labels)
    ]
    chart.categoryAxis.labels.fontSize = 7
    drawing.add(chart)
    return drawing


def render_usage_pdf(institution_name, usage, filters_label):
    """The report as PDF bytes. `usage` is what usage.institution_usage() returned."""
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=40,
        rightMargin=40,
        topMargin=40,
        bottomMargin=40,
        title=f"VRI usage report: {institution_name}",
    )
    styles = getSampleStyleSheet()
    body, heading = styles["Normal"], styles["Heading2"]
    totals, staff = usage["totals"], usage["staff"]
    start, end = usage["period"]["from"], usage["period"]["to"]

    def section(title, *content):
        # A heading never sits alone at the bottom of a page.
        return KeepTogether([Spacer(1, 14), Paragraph(title, heading), *content])

    def note(text):
        return Paragraph(text, body)

    story = [
        Paragraph("VRI Usage Report", styles["Title"]),
        note(f"<b>Institution:</b> {escape(institution_name)}"),
        note(f"<b>Period:</b> {_day(start)} to {_day(end)}"),
        note(f"<b>Filters:</b> {escape(filters_label)}"),
        note(
            f"<b>Generated:</b> {timezone.localtime().strftime('%d %B %Y, %H:%M').lstrip('0')}"
        ),
        section(
            "Summary",
            _table(
                [
                    ["Measure", "Value"],
                    [
                        "Sessions requested",
                        f"{totals['sessions']} ({_change(totals['sessions'], totals['previous_period_sessions'])})",
                    ],
                    [
                        "Completed sessions",
                        f"{totals['completed']} ({totals['completion_rate']}% completion rate)",
                    ],
                    [
                        "Average session duration",
                        _minutes(totals["avg_duration_minutes"]),
                    ],
                    [
                        "Total time with an interpreter",
                        _minutes(totals["total_minutes"]),
                    ],
                    [
                        "Staff who used VRI",
                        f"{staff['used_vri']} of {staff['total']}",
                    ],
                ],
                widths=(0.5, 0.5),
            ),
        ),
    ]

    period_days = (end - start).days + 1
    grouping = "per day" if period_days <= DAILY_UP_TO else "per 7 days"
    story.append(
        section(
            f"Sessions over time ({grouping})",
            _chart(usage["over_time"])
            if totals["sessions"]
            else note("No sessions in this period."),
        )
    )

    def count_section(title, rows, first_column):
        if not any(count for _, count in rows):
            return section(title, note("No sessions in this period."))
        return section(title, _table([[first_column, "Sessions"], *rows]))

    story.append(
        count_section(
            "Sessions by type",
            [(row["label"], row["count"]) for row in usage["by_type"]],
            "Type",
        )
    )
    story.append(
        count_section(
            "Session outcomes",
            [(row["label"], row["count"]) for row in usage["by_outcome"]],
            "Outcome",
        )
    )
    story.append(
        count_section(
            "Sessions by department",
            [
                (row["department"] or "No department", row["count"])
                for row in usage["by_department"]
            ],
            "Department",
        )
    )
    story.append(
        section(
            "Staff usage",
            _table(
                [
                    ["Staff", "Number"],
                    ["Staff with access", staff["total"]],
                    ["Used VRI in this period", staff["used_vri"]],
                    ["Have not used VRI", staff["not_used"]],
                ]
            ),
        )
    )

    document.build(story)
    return buffer.getvalue()
