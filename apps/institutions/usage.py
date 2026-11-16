"""How much an institution uses VRI (V2-029): session counts, types, outcomes and staff usage for a period.

The view decides which requests and staff are counted (the institution, the staff and department filters);
this module only reads the period from the query and counts.
"""

from datetime import date, timedelta

from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.interpreters.models import Specialisation
from apps.service_requests.models import ServiceRequest
from apps.service_requests.serializers import REQUEST_CONTEXTS, session_duration_minutes

DEFAULT_DAYS = 7
MAX_DAYS = 366


def parse_period(params, today=None):
    """The `from` and `to` dates (YYYY-MM-DD, both included). Without them: the last 7 days up to today."""
    today = today or timezone.localdate()
    errors = {}

    def read(name):
        value = params.get(name, "").strip()
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            errors[name] = ["Use a date like 2026-11-14."]
            return None

    start, end = read("from"), read("to")
    if errors:
        raise ValidationError(errors)
    end = end or today
    start = start or end - timedelta(days=DEFAULT_DAYS - 1)
    if start > end:
        raise ValidationError(
            {"from": ["The start date must be on or before the end date."]}
        )
    if (end - start).days + 1 > MAX_DAYS:
        raise ValidationError(
            {"from": [f"Choose a period of at most {MAX_DAYS} days."]}
        )
    return start, end


def _in_period(requests, start, end):
    # Dates are compared in the server's time zone (Kampala), so a request made at 23:30 counts on that day.
    return requests.filter(created_at__date__gte=start, created_at__date__lte=end)


def _counts(queryset, field):
    return dict(
        queryset.order_by()
        .values_list(field)
        .annotate(n=Count("id"))
        .values_list(field, "n")
    )


def _percent(part, whole):
    return round(part * 100 / whole) if whole else 0


def institution_usage(requests, members, start, end):
    """Usage between `start` and `end` (both included).

    `requests`: the ServiceRequests to count (already limited to the institution and any staff filter).
    `members`: the InstitutionStaff the same filters apply to, for the "used VRI / did not" figures.
    """
    in_period = _in_period(requests, start, end)
    days = (end - start).days + 1
    previous = _in_period(
        requests, start - timedelta(days=days), start - timedelta(days=1)
    )

    total = in_period.count()
    by_status = _counts(in_period, "status")
    completed = by_status.get(ServiceRequest.Status.COMPLETED, 0)

    durations = [
        minutes
        for minutes in map(
            session_duration_minutes, in_period.only("assigned_at", "ended_at")
        )
        if minutes is not None
    ]

    by_type = _counts(in_period, "specialisation_required")
    per_day = _counts(in_period.annotate(day=TruncDate("created_at")), "day")
    # A requester who is no longer a staff member (None) counts with "no department" ("").
    by_department = {}
    for department, n in _counts(
        in_period, "requester__institution_staff__job_title"
    ).items():
        by_department[department or ""] = by_department.get(department or "", 0) + n

    # Staff who still have access; someone revoked since is left out of both figures.
    active_members = members.filter(is_active=True)
    staff_total = active_members.count()
    used = active_members.filter(user__in=in_period.values("requester")).count()

    return {
        "period": {"from": start, "to": end},
        "totals": {
            "sessions": total,
            "previous_period_sessions": previous.count(),
            "completed": completed,
            "completion_rate": _percent(completed, total),
            "total_minutes": sum(durations),
            "avg_duration_minutes": round(sum(durations) / len(durations))
            if durations
            else 0,
        },
        # The request types (General, Medical, Legal) always appear, so the screen can show zeros.
        "by_type": [
            {"type": value, "label": label, "count": by_type.get(value, 0)}
            for value, label in Specialisation.choices
            if value in REQUEST_CONTEXTS or by_type.get(value)
        ],
        "by_outcome": [
            {"status": value, "label": label, "count": by_status[value]}
            for value, label in ServiceRequest.Status.choices
            if by_status.get(value)
        ],
        "by_department": sorted(
            (
                {"department": department, "count": n}
                for department, n in by_department.items()
            ),
            key=lambda row: (-row["count"], row["department"]),
        ),
        "over_time": [
            {"date": day, "count": per_day.get(day, 0)}
            for day in (start + timedelta(days=i) for i in range(days))
        ],
        "staff": {
            "total": staff_total,
            "used_vri": used,
            "not_used": staff_total - used,
        },
    }
