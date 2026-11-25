"""Reports for administrators (client report list, section 2.1).

Each report is one read-only endpoint that takes ?days=7, 30 or 90 (default 30) and returns
numbers worked out from data the platform already keeps. Only administrators can read them.

Reports here:
- Usage and trends: how many requests and sessions, per day, by specialisation and by status.
- Response times: how fast requests were answered, how many were abandoned or failed,
  busiest hours, and how interpreters answered their offers.
"""

from datetime import timedelta

from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q
from django.db.models.functions import ExtractHour, TruncDate
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsAdminRole
from apps.service_requests.models import RequestOffer, ServiceRequest

ALLOWED_DAYS = (7, 30, 90)
DEFAULT_DAYS = 30

# A request counts as a session once an interpreter has accepted it.
SESSION_STATUSES = (ServiceRequest.Status.ASSIGNED, ServiceRequest.Status.COMPLETED)

DAYS_PARAMETER = OpenApiParameter(
    "days",
    int,
    description="How many days back to report on: 7, 30 or 90 (default 30).",
)


def read_days(request):
    """The ?days= value, or 30 when it is missing or not one of 7, 30, 90."""
    try:
        days = int(request.query_params.get("days", DEFAULT_DAYS))
    except (TypeError, ValueError):
        return DEFAULT_DAYS
    return days if days in ALLOWED_DAYS else DEFAULT_DAYS


def requests_in_range(days):
    """Service requests made on each of the last `days` days, today included."""
    first_day = timezone.localdate() - timedelta(days=days - 1)
    return ServiceRequest.objects.filter(created_at__date__gte=first_day), first_day


def seconds(duration):
    """A timedelta as whole seconds, or None when there is nothing to average."""
    return None if duration is None else round(duration.total_seconds())


class AdminUsageReportView(APIView):
    """Usage and trends: requests and sessions over the chosen period."""

    permission_classes = [IsAdminRole]

    @extend_schema(
        tags=["Admin reports"],
        summary="Admin report: usage and trends",
        parameters=[DAYS_PARAMETER],
    )
    def get(self, request):
        days = read_days(request)
        requests, first_day = requests_in_range(days)

        status = ServiceRequest.Status
        summary = requests.aggregate(
            requests=Count("id"),
            sessions=Count("id", filter=Q(status__in=SESSION_STATUSES)),
            completed=Count("id", filter=Q(status=status.COMPLETED)),
            cancelled=Count("id", filter=Q(status=status.CANCELLED)),
            failed=Count("id", filter=Q(status=status.FAILED)),
            emergency=Count("id", filter=Q(priority=ServiceRequest.Priority.EMERGENCY)),
        )

        per_day = {
            row["day"]: row
            for row in requests.annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(
                requests=Count("id"),
                sessions=Count("id", filter=Q(status__in=SESSION_STATUSES)),
            )
            .order_by()
        }
        by_day = []
        for offset in range(days):
            day = first_day + timedelta(days=offset)
            row = per_day.get(day)
            by_day.append(
                {
                    "date": day.isoformat(),
                    "requests": row["requests"] if row else 0,
                    "sessions": row["sessions"] if row else 0,
                }
            )

        by_specialisation = [
            {"specialisation": row["specialisation_required"], "count": row["total"]}
            for row in requests.values("specialisation_required")
            .annotate(total=Count("id"))
            .order_by("-total", "specialisation_required")
        ]
        by_status = [
            {"status": row["status"], "count": row["total"]}
            for row in requests.values("status")
            .annotate(total=Count("id"))
            .order_by("-total", "status")
        ]

        return Response(
            {
                "range_days": days,
                "summary": summary,
                "by_day": by_day,
                "by_specialisation": by_specialisation,
                "by_status": by_status,
            }
        )


class AdminResponseTimesReportView(APIView):
    """Response times: how quickly requests were answered, and what was lost."""

    permission_classes = [IsAdminRole]

    @extend_schema(
        tags=["Admin reports"],
        summary="Admin report: response times",
        parameters=[DAYS_PARAMETER],
    )
    def get(self, request):
        days = read_days(request)
        requests, _ = requests_in_range(days)

        duration = DurationField()
        status = ServiceRequest.Status
        timings = requests.aggregate(
            requests=Count("id"),
            answered=Count("id", filter=Q(assigned_at__isnull=False)),
            # Cancelled by the requester before any interpreter accepted.
            abandoned=Count(
                "id", filter=Q(status=status.CANCELLED, assigned_at__isnull=True)
            ),
            # Nobody could take it in time.
            failed=Count("id", filter=Q(status=status.FAILED)),
            # Sent to a specialist's fallback (a general interpreter was offered instead).
            matched_by_fallback=Count("id", filter=Q(matched_by_fallback=True)),
            avg_time_to_accept=Avg(
                ExpressionWrapper(
                    F("assigned_at") - F("created_at"), output_field=duration
                ),
                filter=Q(assigned_at__isnull=False),
            ),
            avg_queue_time=Avg(
                ExpressionWrapper(
                    F("offered_at") - F("created_at"), output_field=duration
                ),
                filter=Q(offered_at__isnull=False),
            ),
        )

        per_hour = {
            row["hour"]: row["total"]
            for row in requests.annotate(hour=ExtractHour("created_at"))
            .values("hour")
            .annotate(total=Count("id"))
            .order_by()
        }

        offers = RequestOffer.objects.filter(
            offered_at__date__gte=timezone.localdate() - timedelta(days=days - 1)
        ).aggregate(
            total=Count("id"),
            accepted=Count("id", filter=Q(outcome=RequestOffer.Outcome.ACCEPTED)),
            declined=Count("id", filter=Q(outcome=RequestOffer.Outcome.DECLINED)),
            expired=Count("id", filter=Q(outcome=RequestOffer.Outcome.EXPIRED)),
        )
        answered_offers = offers["accepted"] + offers["declined"] + offers["expired"]

        return Response(
            {
                "range_days": days,
                "summary": {
                    "requests": timings["requests"],
                    "answered": timings["answered"],
                    "abandoned": timings["abandoned"],
                    "failed": timings["failed"],
                    "matched_by_fallback": timings["matched_by_fallback"],
                    "avg_seconds_to_accept": seconds(timings["avg_time_to_accept"]),
                    "avg_seconds_in_queue": seconds(timings["avg_queue_time"]),
                },
                "peak_hours": [
                    {"hour": hour, "requests": per_hour.get(hour, 0)}
                    for hour in range(24)
                ],
                "offers": {
                    **offers,
                    # Accepted offers out of all offers that got an answer or ran out of time.
                    "acceptance_rate_percent": (
                        round(offers["accepted"] * 100 / answered_offers)
                        if answered_offers
                        else None
                    ),
                },
            }
        )
