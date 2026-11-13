"""Numbers behind the charts on the administrator dashboard.

Every number here is worked out from data the platform already keeps (users, interpreter
profiles, service requests, the audit log). All of them are for administrators only.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.db.models.functions import TruncDate, TruncMonth
from django.utils import timezone
from django.utils.timesince import timesince
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import AuditLog
from apps.core.permissions import IsAdminRole
from apps.interpreters.models import InterpreterProfile, VerificationStatus
from apps.service_requests.models import OPEN_STATUSES, ServiceRequest

User = get_user_model()

GROWTH_MONTHS = 6
ACTIVITY_DAYS = 7
FEED_LIMIT = 10

# A request counts as a session once an interpreter has accepted it.
SESSION_STATUSES = (ServiceRequest.Status.ASSIGNED, ServiceRequest.Status.COMPLETED)

ChartPointsSerializer = inline_serializer(
    "AdminChartPoints",
    {
        "results": inline_serializer(
            "AdminChartPoint",
            {"label": serializers.CharField(), "value": serializers.IntegerField()},
            many=True,
        )
    },
)


def month_start(today, months_back):
    """The first day of the month that is `months_back` months before `today`'s month."""
    index = today.year * 12 + (today.month - 1) - months_back
    return today.replace(year=index // 12, month=index % 12 + 1, day=1)


def time_ago(moment):
    if timezone.now() - moment < timedelta(minutes=1):
        return "Just now"
    return f"{timesince(moment, depth=1)} ago"


class AdminUserGrowthView(APIView):
    """Active users on the platform at the end of each of the last six months."""

    permission_classes = [IsAdminRole]

    @extend_schema(
        tags=["Dashboards"],
        summary="Administrator dashboard: user growth",
        responses={200: ChartPointsSerializer},
    )
    def get(self, request):
        users = User.objects.filter(is_active=True)
        this_month = timezone.localtime().replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        first_month = month_start(this_month, GROWTH_MONTHS - 1)

        running_total = users.filter(date_joined__lt=first_month).count()
        joined_per_month = {
            (row["month"].year, row["month"].month): row["total"]
            for row in users.filter(date_joined__gte=first_month)
            .annotate(month=TruncMonth("date_joined"))
            .values("month")
            .annotate(total=Count("id"))
            .order_by()
        }

        points = []
        for months_back in range(GROWTH_MONTHS - 1, -1, -1):
            month = month_start(this_month, months_back)
            running_total += joined_per_month.get((month.year, month.month), 0)
            points.append({"label": month.strftime("%b"), "value": running_total})
        return Response({"results": points})


class AdminSessionsActivityView(APIView):
    """Sessions that started on each of the last seven days."""

    permission_classes = [IsAdminRole]

    @extend_schema(
        tags=["Dashboards"],
        summary="Administrator dashboard: sessions per day",
        responses={
            200: inline_serializer(
                "AdminSessionsActivity",
                {
                    "labels": serializers.ListField(child=serializers.CharField()),
                    "values": serializers.ListField(child=serializers.IntegerField()),
                },
            )
        },
    )
    def get(self, request):
        first_day = timezone.localdate() - timedelta(days=ACTIVITY_DAYS - 1)
        per_day = {
            row["day"]: row["total"]
            for row in ServiceRequest.objects.filter(
                status__in=SESSION_STATUSES, assigned_at__date__gte=first_day
            )
            .annotate(day=TruncDate("assigned_at"))
            .values("day")
            .annotate(total=Count("id"))
            .order_by()
        }

        days = [first_day + timedelta(days=i) for i in range(ACTIVITY_DAYS)]
        return Response(
            {
                "labels": [day.strftime("%a") for day in days],
                "values": [per_day.get(day, 0) for day in days],
            }
        )


class AdminSessionTypesView(APIView):
    """Sessions held, and requests still waiting for an interpreter.

    The platform only does video relay sessions for now, so `onsite` is always 0.
    """

    permission_classes = [IsAdminRole]

    @extend_schema(
        tags=["Dashboards"],
        summary="Administrator dashboard: session types",
        responses={
            200: inline_serializer(
                "AdminSessionTypes",
                {
                    "vri": serializers.IntegerField(),
                    "onsite": serializers.IntegerField(),
                    "pending": serializers.IntegerField(),
                    "total": serializers.IntegerField(),
                },
            )
        },
    )
    def get(self, request):
        counts = ServiceRequest.objects.aggregate(
            vri=Count("id", filter=Q(status__in=SESSION_STATUSES)),
            pending=Count("id", filter=Q(status__in=OPEN_STATUSES)),
        )
        return Response(
            {
                "vri": counts["vri"],
                "onsite": 0,
                "pending": counts["pending"],
                "total": counts["vri"] + counts["pending"],
            }
        )


class AdminVerificationStatusView(APIView):
    """How many interpreters are approved, waiting for review, rejected or switched off.

    `flagged` is interpreters an administrator switched off after approving them.
    """

    permission_classes = [IsAdminRole]

    @extend_schema(
        tags=["Dashboards"],
        summary="Administrator dashboard: interpreter verification status",
        responses={
            200: inline_serializer(
                "AdminVerificationStatus",
                {
                    "approved": serializers.IntegerField(),
                    "pending": serializers.IntegerField(),
                    "rejected": serializers.IntegerField(),
                    "flagged": serializers.IntegerField(),
                    "total": serializers.IntegerField(),
                },
            )
        },
    )
    def get(self, request):
        counts = dict(
            InterpreterProfile.objects.values_list("verification_status")
            .annotate(total=Count("id"))
            .order_by()
        )
        approved = counts.get(VerificationStatus.APPROVED, 0)
        pending = counts.get(VerificationStatus.INACTIVE, 0)
        rejected = counts.get(VerificationStatus.REJECTED, 0)
        flagged = counts.get(VerificationStatus.DEACTIVATED, 0)
        return Response(
            {
                "approved": approved,
                "pending": pending,
                "rejected": rejected,
                "flagged": flagged,
                "total": approved + pending + rejected + flagged,
            }
        )


class AdminActivityFeedView(APIView):
    """The latest things recorded in the audit log, as short sentences."""

    permission_classes = [IsAdminRole]

    @extend_schema(
        tags=["Dashboards"],
        summary="Administrator dashboard: recent activity",
        responses={
            200: inline_serializer(
                "AdminActivityItem",
                {
                    "text": serializers.CharField(),
                    "time": serializers.CharField(),
                    "created_at": serializers.DateTimeField(),
                },
                many=True,
            )
        },
    )
    def get(self, request):
        entries = AuditLog.objects.select_related("user")[:FEED_LIMIT]
        return Response(
            [
                {
                    "text": (
                        f"{entry.user.full_name}: {entry.get_event_display()}"
                        if entry.user
                        else entry.get_event_display()
                    ),
                    "time": time_ago(entry.created_at),
                    "created_at": entry.created_at,
                }
                for entry in entries
            ]
        )
