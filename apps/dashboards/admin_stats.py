"""Numbers behind the charts on the administrator dashboard.

Every number here is worked out from data the platform already keeps (users, interpreter
profiles, service requests, the audit log). All of them are for administrators only.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Avg, Count, Q
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
from apps.realtime.ratings import deaf_completed_session_feedback
from apps.service_requests.models import OPEN_STATUSES, ServiceRequest

User = get_user_model()

GROWTH_MONTHS = 6
ACTIVITY_DAYS = 7
FEED_LIMIT = 10
PERFORMANCE_LOW_RATING_LIMIT = 25
PERFORMANCE_MONTHS = 12

AdminInterpreterPerformanceSerializer = inline_serializer(
    "AdminInterpreterPerformance",
    {
        "summary": inline_serializer(
            "AdminInterpreterPerformanceSummary",
            {
                "average_rating": serializers.FloatField(allow_null=True),
                "ratings_count": serializers.IntegerField(),
                "low_rating_count": serializers.IntegerField(),
                "completed_sessions": serializers.IntegerField(),
            },
        ),
        "trends": inline_serializer(
            "AdminInterpreterRatingTrend",
            {
                "month": serializers.CharField(),
                "average_rating": serializers.FloatField(allow_null=True),
                "rating_count": serializers.IntegerField(),
            },
            many=True,
        ),
        "interpreters": inline_serializer(
            "AdminInterpreterPerformanceRow",
            {
                "interpreter_id": serializers.IntegerField(),
                "interpreter_name": serializers.CharField(),
                "specialisation": serializers.CharField(),
                "average_rating": serializers.FloatField(allow_null=True),
                "rating_count": serializers.IntegerField(),
                "completed_sessions": serializers.IntegerField(),
                "low_rating_count": serializers.IntegerField(),
            },
            many=True,
        ),
        "low_ratings": inline_serializer(
            "AdminLowRatingFeedback",
            {
                "id": serializers.IntegerField(),
                "interpreter_name": serializers.CharField(),
                "session_date": serializers.DateTimeField(allow_null=True),
                "rating": serializers.IntegerField(),
                "comment": serializers.CharField(allow_blank=True),
            },
            many=True,
        ),
    },
)

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


class AdminInterpreterPerformanceView(APIView):
    """Interpreter performance from Deaf requester feedback and completed calls."""

    permission_classes = [IsAdminRole]

    @extend_schema(
        tags=["Dashboards"],
        summary="Administrator dashboard: interpreter performance",
        description=(
            "Aggregated ratings are based only on Deaf requester feedback for ended calls "
            "whose service request is COMPLETED. Feedback comments contain no participant identity."
        ),
        responses={200: AdminInterpreterPerformanceSerializer},
    )
    def get(self, request):
        ratings = deaf_completed_session_feedback()
        summary = ratings.aggregate(
            average_rating=Avg("rating"),
            ratings_count=Count("id"),
            low_rating_count=Count("id", filter=Q(rating__lte=2)),
        )
        completed_requests = ServiceRequest.objects.filter(
            status=ServiceRequest.Status.COMPLETED,
            video_session__status="ended",
            interpreter__isnull=False,
        )
        summary["completed_sessions"] = (
            completed_requests.values("interpreter_id", "id").distinct().count()
        )

        per_interpreter_ratings = {
            row["interpreter_id"]: row
            for row in ratings.values("interpreter_id").annotate(
                average_rating=Avg("rating"),
                rating_count=Count("id"),
                low_rating_count=Count("id", filter=Q(rating__lte=2)),
            )
        }
        per_interpreter_sessions = dict(
            completed_requests.values("interpreter_id")
            .annotate(total=Count("id", distinct=True))
            .values_list("interpreter_id", "total")
        )
        interpreters = []
        for profile in InterpreterProfile.objects.select_related("user").all():
            rating_data = per_interpreter_ratings.get(profile.pk, {})
            interpreters.append(
                {
                    "interpreter_id": profile.pk,
                    "interpreter_name": profile.user.full_name,
                    "specialisation": ", ".join(profile.specialisation_labels()),
                    "average_rating": (
                        float(rating_data["average_rating"])
                        if rating_data.get("average_rating") is not None
                        else None
                    ),
                    "rating_count": rating_data.get("rating_count", 0),
                    "completed_sessions": per_interpreter_sessions.get(profile.pk, 0),
                    "low_rating_count": rating_data.get("low_rating_count", 0),
                }
            )
        interpreters.sort(
            key=lambda row: (
                row["average_rating"] is not None,
                row["average_rating"] or 0,
                row["rating_count"],
            ),
            reverse=True,
        )

        now = timezone.localtime()
        first_month = month_start(now.replace(day=1), PERFORMANCE_MONTHS - 1)
        monthly_ratings = {
            (row["month"].year, row["month"].month): row
            for row in ratings.filter(
                video_session__service_request__ended_at__gte=first_month,
                video_session__service_request__ended_at__lt=month_start(
                    now.replace(day=1), -1
                ),
            )
            .annotate(month=TruncMonth("video_session__service_request__ended_at"))
            .values("month")
            .annotate(average_rating=Avg("rating"), rating_count=Count("id"))
        }
        trends = []
        for months_back in range(PERFORMANCE_MONTHS - 1, -1, -1):
            month = month_start(now.replace(day=1), months_back)
            row = monthly_ratings.get((month.year, month.month), {})
            trends.append(
                {
                    "month": month.strftime("%Y-%m"),
                    "average_rating": (
                        float(row["average_rating"])
                        if row.get("average_rating") is not None
                        else None
                    ),
                    "rating_count": row.get("rating_count", 0),
                }
            )

        low_ratings = [
            {
                "id": feedback.pk,
                "interpreter_name": feedback.interpreter.user.full_name,
                "session_date": feedback.video_session.service_request.ended_at,
                "rating": feedback.rating,
                "comment": feedback.comment,
            }
            for feedback in ratings.filter(rating__lte=2)
            .select_related("interpreter__user", "video_session__service_request")
            .order_by("-video_session__service_request__ended_at", "-pk")[
                :PERFORMANCE_LOW_RATING_LIMIT
            ]
        ]
        return Response(
            {
                "summary": {
                    "average_rating": (
                        float(summary["average_rating"])
                        if summary["average_rating"] is not None
                        else None
                    ),
                    "ratings_count": summary["ratings_count"],
                    "low_rating_count": summary["low_rating_count"],
                    "completed_sessions": summary["completed_sessions"],
                },
                "trends": trends,
                "interpreters": interpreters,
                "low_ratings": low_ratings,
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
