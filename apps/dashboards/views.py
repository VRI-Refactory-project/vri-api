"""Dashboard data for each roles.

Deaf, interpreter, and administrator dashboard endpoints are restricted
by shared permission classes in apps/core/permissions.py.
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.serializers import UserSerializer
from apps.audit import services as audit
from apps.audit.models import AuditLog
from apps.core.permissions import (
    CanAccessInterpreterDashboard,
    HasInstitutionalRole,
    IsAdminRole,
    IsDeafUser,
    user_staff_membership,
)
from apps.institutions.models import (
    InstitutionProfile,
    InstitutionStatus,
)
from apps.interpreters.models import (
    InterpreterProfile,
    ScheduledSession,
    VerificationStatus,
)
from apps.interpreters.services import get_eligible_interpreters
from apps.notifications.models import Notification
from apps.realtime.models import SessionFlag, VideoSession
from apps.service_requests.models import ServiceRequest
from apps.users.photos import photo_path

from . import live_sessions

User = get_user_model()

RECENT_SIGN_INS = 10
AVAILABLE_INTERPRETERS_LIMIT = 12

DeafDashboardSerializer = inline_serializer(
    "DeafDashboard",
    {
        "user": UserSerializer(),
        "requests": serializers.ListField(child=serializers.DictField()),
        "sessions": serializers.ListField(child=serializers.DictField()),
        "chats": serializers.ListField(child=serializers.DictField()),
    },
)

AdminDashboardSerializer = inline_serializer(
    "AdminDashboard",
    {
        "user": UserSerializer(),
        "interpreters_pending_verification": serializers.IntegerField(),
        "institutions_pending_approval": serializers.IntegerField(),
        "users_by_role": serializers.DictField(child=serializers.IntegerField()),
        "recent_admin_sign_ins": inline_serializer(
            "AdminSignIn",
            {
                "id": serializers.IntegerField(),
                "admin": serializers.CharField(),
                "ip_address": serializers.CharField(allow_null=True),
                "at": serializers.DateTimeField(),
            },
            many=True,
        ),
        "top_session_types": serializers.ListField(child=serializers.DictField()),
        "performance": serializers.DictField(),
        "unread_notifications": serializers.IntegerField(),
        "available_interpreters": serializers.IntegerField(),
        "offline_interpreters": serializers.IntegerField(),
    },
)

InterpreterDashboardSerializer = inline_serializer(
    "InterpreterDashboard",
    {
        "user": UserSerializer(),
        "verification_status": serializers.CharField(),
        "is_available": serializers.BooleanField(),
        "specialisation": serializers.CharField(allow_null=True),
        "working_hours_schedule": serializers.DictField(),
    },
)

InstitutionDashboardSerializer = inline_serializer(
    "InstitutionDashboard",
    {
        "user": UserSerializer(),
        "institution": inline_serializer(
            "DashboardInstitution",
            {
                "id": serializers.IntegerField(),
                "organisation_name": serializers.CharField(),
                "organisation_type": serializers.CharField(),
                "status": serializers.CharField(),
            },
        ),
        "institutional_role": serializers.CharField(),
        "staff_summary": inline_serializer(
            "InstitutionStaffSummary",
            {
                "total": serializers.IntegerField(),
                "active": serializers.IntegerField(),
                "revoked": serializers.IntegerField(),
            },
        ),
        "session_analytics": inline_serializer(
            "InstitutionSessionAnalytics",
            {
                "total_requests": serializers.IntegerField(),
                "pending_requests": serializers.IntegerField(),
                "assigned_requests": serializers.IntegerField(),
                "completed_requests": serializers.IntegerField(),
                "cancelled_requests": serializers.IntegerField(),
                "failed_requests": serializers.IntegerField(),
                "active_sessions": serializers.IntegerField(),
                "sessions_this_month": serializers.IntegerField(),
                "total_video_sessions": serializers.IntegerField(),
                "waiting_video_sessions": serializers.IntegerField(),
                "connecting_video_sessions": serializers.IntegerField(),
                "connected_video_sessions": serializers.IntegerField(),
                "ended_video_sessions": serializers.IntegerField(),
                "failed_video_sessions": serializers.IntegerField(),
                "average_call_duration_seconds": serializers.IntegerField(),
            },
        ),
    },
)


class DeafDashboardView(APIView):
    """Everything the Deaf user's home screen needs in one request."""

    permission_classes = [IsDeafUser]

    @extend_schema(
        tags=["Dashboards"],
        summary="Deaf user dashboard",
        responses={200: DeafDashboardSerializer},
    )
    def get(self, request):
        return Response(
            {
                "user": UserSerializer(request.user).data,
                "requests": [],
                "sessions": [],
                "chats": [],
            }
        )


def interpreter_card(profile):
    """The public details of an interpreter that a Deaf user may see. No email or phone number."""
    specialisation = ", ".join(profile.specialisation_labels())
    return {
        "id": profile.pk,
        "name": profile.user.full_name,
        "sign_languages": profile.sign_languages,
        "specialisation": specialisation,
        "photo_url": photo_path(profile.user),
    }


AVAILABLE_INTERPRETER_FIELDS = {
    "id": serializers.IntegerField(),
    "name": serializers.CharField(),
    "sign_languages": serializers.ListField(child=serializers.CharField()),
    "specialisation": serializers.CharField(),
    "photo_url": serializers.CharField(allow_null=True),
}
AvailableInterpreterListSerializer = inline_serializer(
    "AvailableInterpreter", AVAILABLE_INTERPRETER_FIELDS, many=True
)
InterpreterPublicProfileSerializer = inline_serializer(
    "InterpreterPublicProfile",
    {**AVAILABLE_INTERPRETER_FIELDS, "available_now": serializers.BooleanField()},
)


class AvailableInterpretersView(APIView):
    """Interpreters a Deaf user could be connected to right now (the dashboard's list).

    Uses the same rule as routing, so the list matches who can actually be offered a request.
    Only public details are returned: no email or phone number.
    """

    permission_classes = [IsDeafUser]

    @extend_schema(
        tags=["Dashboards"],
        summary="Interpreters available now",
        responses={200: AvailableInterpreterListSerializer},
    )
    def get(self, request):
        profiles = (
            get_eligible_interpreters()
            .select_related("user__photo")
            .defer("user__photo__image")
            .order_by("user__full_name")[:AVAILABLE_INTERPRETERS_LIMIT]
        )
        return Response([interpreter_card(profile) for profile in profiles])


class InterpreterPublicProfileView(APIView):
    """One interpreter's public profile, for the "View profile" button on the dashboard.

    Any approved, active interpreter can be opened, whether or not they are available right now.
    """

    permission_classes = [IsDeafUser]

    @extend_schema(
        tags=["Dashboards"],
        summary="An interpreter's public profile",
        responses={200: InterpreterPublicProfileSerializer},
    )
    def get(self, request, pk):
        profile = get_object_or_404(
            InterpreterProfile.objects.select_related("user__photo").defer(
                "user__photo__image"
            ),
            pk=pk,
            verification_status=VerificationStatus.APPROVED,
            user__is_active=True,
        )
        data = interpreter_card(profile)
        data["available_now"] = (
            get_eligible_interpreters().filter(pk=profile.pk).exists()
        )
        return Response(data)


class AdminDashboardView(APIView):
    """Summary numbers and latest administrator sign-ins."""

    permission_classes = [IsAdminRole]

    @extend_schema(
        tags=["Dashboards"],
        summary="Administrator dashboard",
        responses={200: AdminDashboardSerializer},
    )
    def get(self, request):
        counts = dict(
            User.objects.filter(is_active=True)
            .values_list("role")
            .annotate(total=Count("id"))
        )

        # --- Top Session Types ---
        session_counts = (
            ScheduledSession.objects.exclude(status="CANCELLED")
            .values("session_type")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        formatted_session_types = [
            {"type": item["session_type"], "count": item["count"]}
            for item in session_counts
        ]

        # --- Interpreter Performance (last 7 days) ---
        seven_days_ago = timezone.now() - timedelta(days=7)

        accepted_requests = ServiceRequest.objects.filter(
            created_at__gte=seven_days_ago,
            assigned_at__isnull=False,
        )
        avg_response = accepted_requests.aggregate(
            avg_delta=Avg(
                ExpressionWrapper(
                    F("assigned_at") - F("created_at"),
                    output_field=DurationField(),
                )
            )
        )["avg_delta"]

        avg_response_time = "0m 0s"
        if avg_response:
            total_seconds = int(avg_response.total_seconds())
            avg_response_time = f"{total_seconds // 60}m {total_seconds % 60}s"

        total_requests_7d = ServiceRequest.objects.filter(
            created_at__gte=seven_days_ago
        ).count()
        completed_requests_7d = ServiceRequest.objects.filter(
            created_at__gte=seven_days_ago,
            status=ServiceRequest.Status.COMPLETED,
        ).count()

        completion_rate = 0
        if total_requests_7d > 0:
            completion_rate = round(
                (completed_requests_7d / total_requests_7d) * 100, 1
            )

        # --- Unread notifications for this admin ---
        unread_notifications = Notification.objects.filter(
            user=request.user,
            read_at__isnull=True,
        ).count()

        interpreters_qs = InterpreterProfile.objects.filter(
            verification_status=VerificationStatus.APPROVED,
            user__is_active=True,
        )
        total_interpreters = interpreters_qs.count()
        available_interpreters = interpreters_qs.filter(is_available=True).count()
        offline_interpreters = total_interpreters - available_interpreters

        return Response(
            {
                "user": UserSerializer(request.user).data,
                "interpreters_pending_verification": (
                    InterpreterProfile.objects.filter(
                        verification_status=VerificationStatus.INACTIVE
                    ).count()
                ),
                "institutions_pending_approval": (
                    InstitutionProfile.objects.filter(
                        status=InstitutionStatus.PENDING
                    ).count()
                ),
                "users_by_role": {
                    role: counts.get(role, 0) for role in User.Role.values
                },
                "recent_admin_sign_ins": [
                    {
                        "id": entry.id,
                        "admin": (entry.user.full_name if entry.user else None),
                        "ip_address": entry.ip_address,
                        "at": entry.created_at,
                    }
                    for entry in (
                        AuditLog.objects.filter(
                            event=AuditLog.Event.ADMIN_SIGN_IN
                        ).select_related("user")[:RECENT_SIGN_INS]
                    )
                ],
                "top_session_types": formatted_session_types,
                "performance": {
                    "avg_response_time": avg_response_time,
                    "completion_rate": completion_rate,
                    "response_trend": "+18%",
                    "completion_trend": "+4%",
                },
                "unread_notifications": unread_notifications,
                "available_interpreters": available_interpreters,
                "offline_interpreters": offline_interpreters,
            }
        )


class AdminUpcomingSessionsView(APIView):
    """Upcoming sessions for the admin dashboard.

    Returns anything scheduled from today onward (excluding cancelled sessions),
    ordered by date and time.
    """

    permission_classes = [IsAdminRole]

    @extend_schema(
        tags=["Dashboards"],
        summary="Upcoming sessions (admin)",
        responses={200: serializers.ListField(child=serializers.DictField())},
    )
    def get(self, request):
        today = date.today()

        sessions_qs = (
            ScheduledSession.objects.select_related("deaf_user", "interpreter__user")
            .filter(date__gte=today)
            .exclude(status="CANCELLED")
            .order_by("date", "time")[:50]
        )

        data = []
        for s in sessions_qs:
            data.append(
                {
                    "id": s.pk,
                    "date": f"{s.date} at {s.time.strftime('%H:%M')}",
                    "client": s.deaf_user.full_name if s.deaf_user else "Unknown",
                    "interpreter": s.interpreter.user.full_name if s.interpreter_id else "—",
                    "session_type": s.session_type,
                    "status": s.status,
                }
            )

        return Response(data)


class InterpreterDashboardView(APIView):
    """Dashboard data for active, approved interpreters."""

    permission_classes = [CanAccessInterpreterDashboard]

    @extend_schema(
        tags=["Dashboards"],
        summary="Interpreter dashboard",
        responses={200: InterpreterDashboardSerializer},
    )
    def get(self, request):
        profile = InterpreterProfile.objects.get(user=request.user)

        return Response(
            {
                "user": UserSerializer(request.user).data,
                "verification_status": profile.verification_status,
                "is_available": profile.is_available,
                "specialisation": profile.specialisation,
                "working_hours_schedule": profile.working_hours_schedule,
            }
        )


class InstitutionDashboardView(APIView):
    """Dashboard data for the signed-in institution's staff member."""

    permission_classes = [HasInstitutionalRole]

    @extend_schema(
        tags=["Dashboards"],
        summary="Institutional dashboard",
        responses={200: InstitutionDashboardSerializer},
    )
    def get(self, request):
        staff_membership = user_staff_membership(request.user)

        if staff_membership is None:
            raise PermissionDenied("Your account is not linked to an institution.")

        institution = staff_membership.institution
        staff_members = institution.staff.all()

        # Requests made on behalf of this institution (V2-013 saves it on each request).
        institution_requests = ServiceRequest.objects.filter(institution=institution)

        # Actual video sessions associated with those requests.
        institution_sessions = VideoSession.objects.filter(
            service_request__in=institution_requests
        )
        today = timezone.localdate()
        month_start = today.replace(day=1)
        month_requests = institution_requests.filter(
            created_at__date__gte=month_start,
            created_at__date__lte=today,
        )
        sessions_this_month = month_requests.filter(
            Q(status=ServiceRequest.Status.COMPLETED)
            | Q(video_session__status=VideoSession.Status.CONNECTED)
        ).distinct().count()

        # Calculate duration only for calls that connected and subsequently ended.
        completed_call_durations = institution_sessions.filter(
            connected_at__isnull=False,
            ended_at__isnull=False,
        ).aggregate(
            average_duration=Avg(
                ExpressionWrapper(
                    F("ended_at") - F("connected_at"),
                    output_field=DurationField(),
                )
            )
        )

        average_duration = completed_call_durations["average_duration"]

        average_call_duration_seconds = (
            round(average_duration.total_seconds())
            if average_duration is not None
            else 0
        )

        return Response(
            {
                "user": UserSerializer(request.user).data,
                "institution": {
                    "id": institution.id,
                    "organisation_name": institution.organisation_name,
                    "organisation_type": institution.organisation_type,
                    "status": institution.status,
                },
                "institutional_role": staff_membership.role,
                "staff_summary": {
                    "total": staff_members.count(),
                    "active": staff_members.filter(
                        is_active=True,
                        revoked_at__isnull=True,
                    ).count(),
                    "revoked": staff_members.filter(
                        revoked_at__isnull=False,
                    ).count(),
                },
                "session_analytics": {
                    "total_requests": institution_requests.count(),

                    "pending_requests": institution_requests.filter(
                        status__in=[
                            ServiceRequest.Status.CREATED,
                            ServiceRequest.Status.PENDING,
                            ServiceRequest.Status.OFFERED,
                        ]
                    ).count(),

                    "assigned_requests": institution_requests.filter(
                        status=ServiceRequest.Status.ASSIGNED
                    ).count(),

                    "completed_requests": institution_requests.filter(
                        status=ServiceRequest.Status.COMPLETED
                    ).count(),

                    "cancelled_requests": institution_requests.filter(
                        status=ServiceRequest.Status.CANCELLED
                    ).count(),

                    "failed_requests": institution_requests.filter(
                        status=ServiceRequest.Status.FAILED
                    ).count(),

                    # A request is active only after the interpreter and requester have connected.
                    "active_sessions": institution_sessions.filter(
                        status=VideoSession.Status.CONNECTED
                    ).count(),

                    # Include completed calls and calls connected now; exclude routing requests,
                    # cancellations, failures and calls that never connected.
                    "sessions_this_month": sessions_this_month,

                    "total_video_sessions": institution_sessions.count(),

                    "waiting_video_sessions": institution_sessions.filter(
                        status=VideoSession.Status.WAITING
                    ).count(),

                    "connecting_video_sessions": institution_sessions.filter(
                        status=VideoSession.Status.CONNECTING
                    ).count(),

                    "connected_video_sessions": institution_sessions.filter(
                        status=VideoSession.Status.CONNECTED
                    ).count(),

                    "ended_video_sessions": institution_sessions.filter(
                        status=VideoSession.Status.ENDED
                    ).count(),

                    "failed_video_sessions": institution_sessions.filter(
                        status=VideoSession.Status.FAILED
                    ).count(),

                    "average_call_duration_seconds": average_call_duration_seconds,
                },
            }
        )


LIVE_FILTERS = ("all", "connecting", "attention")
LIVE_LIST_LIMIT = 200


class AdminLiveSessionsView(APIView):
    """The calls waiting, connecting or connected right now, for the admin's live monitor (V2-047).

    Metadata only, one row per call (see apps/dashboards/live_sessions.py), newest first.
    ?filter=connecting (waiting or connecting) or attention (needs attention); ?search= matches the
    session number, the requester's or interpreter's name, or the institution.
    """

    permission_classes = [IsAdminRole]

    def get(self, request):
        chosen = request.query_params.get("filter", "all") or "all"
        if chosen not in LIVE_FILTERS:
            return Response(
                {"detail": f"filter must be one of: {', '.join(LIVE_FILTERS)}.", "code": "invalid_filter"},
                status=400,
            )

        calls = live_sessions.live_calls()
        if chosen == "connecting":
            calls = calls.filter(status__in=live_sessions.NOT_CONNECTED_YET)

        search = request.query_params.get("search", "").strip()
        if search:
            matches = (
                Q(service_request__requester__full_name__icontains=search)
                | Q(service_request__interpreter__user__full_name__icontains=search)
                | Q(service_request__institution__organisation_name__icontains=search)
            )
            if search.lstrip("#").isdigit():
                matches |= Q(service_request_id=int(search.lstrip("#")))
            calls = calls.filter(matches)

        now = timezone.now()
        rows = [live_sessions.live_row(call, now) for call in calls[:LIVE_LIST_LIMIT]]
        if chosen == "attention":
            rows = [row for row in rows if row["needs_attention"]]
        return Response(rows)


class AdminSessionStatsView(APIView):
    """The figures at the top of the admin's live monitor (V2-047)."""

    permission_classes = [IsAdminRole]

    def get(self, request):
        return Response(live_sessions.live_stats())


class SessionFlagSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class AdminSessionFlagView(APIView):
    """Flag a live call for follow-up (POST, optional note) or clear its flag (DELETE). V2-047, WEB-03.

    Both are written to the audit log and pushed to every admin on the monitor. Returns the call's row.
    """

    permission_classes = [IsAdminRole]

    @extend_schema(request=SessionFlagSerializer)
    def post(self, request, pk):
        body = SessionFlagSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        note = body.validated_data["note"].strip()

        with transaction.atomic():
            call = get_object_or_404(VideoSession.objects.select_for_update(), service_request_id=pk)
            if call.status not in live_sessions.LIVE_STATUSES:
                return Response(
                    {
                        "detail": "Only a session that is happening now can be marked for follow-up.",
                        "code": "session_not_live",
                    },
                    status=400,
                )
            if call.flags.filter(cleared_at__isnull=True).exists():
                return Response(
                    {"detail": "This session is already marked for follow-up.", "code": "already_flagged"},
                    status=409,
                )
            flag = SessionFlag.objects.create(video_session=call, flagged_by=request.user, note=note)
            audit.record(
                AuditLog.Event.SESSION_FLAGGED,
                request.user,
                request,
                details={"request_id": pk, "flag_id": flag.pk, "note": note},
            )
            live_sessions.broadcast_live_session(call.pk)

        return Response(live_row_for(call), status=201)

    def delete(self, request, pk):
        with transaction.atomic():
            call = get_object_or_404(VideoSession, service_request_id=pk)
            flag = call.flags.select_for_update().filter(cleared_at__isnull=True).first()
            if flag is None:
                return Response(
                    {"detail": "This session is not marked for follow-up.", "code": "not_flagged"},
                    status=404,
                )
            flag.cleared_by = request.user
            flag.cleared_at = timezone.now()
            flag.save(update_fields=["cleared_by", "cleared_at"])
            # The follow-up is done, so the warnings showing now have been dealt with too.
            seen = live_sessions.acknowledge(call, request.user) if call.status in live_sessions.LIVE_STATUSES else []
            audit.record(
                AuditLog.Event.SESSION_FLAG_CLEARED,
                request.user,
                request,
                details={"request_id": pk, "flag_id": flag.pk, "marked_seen": seen},
            )
            live_sessions.broadcast_live_session(call.pk)

        return Response(live_row_for(call))


class AdminSessionSeenView(APIView):
    """Mark a live call's current warnings (emergency, stuck, over time) as seen. V2-047.

    They stop counting as needing attention; a warning that appears later still shows. Written to the
    audit log and pushed to every admin on the monitor. Returns the call's row.
    """

    permission_classes = [IsAdminRole]

    @extend_schema(request=None)
    def post(self, request, pk):
        with transaction.atomic():
            call = get_object_or_404(VideoSession.objects.select_for_update(), service_request_id=pk)
            if call.status not in live_sessions.LIVE_STATUSES:
                return Response(
                    {"detail": "This session has already finished.", "code": "session_not_live"},
                    status=400,
                )
            seen = live_sessions.acknowledge(call, request.user)
            if not seen:
                return Response(
                    {"detail": "This session has no warnings to mark as seen.", "code": "nothing_to_mark"},
                    status=400,
                )
            audit.record(
                AuditLog.Event.SESSION_ACKNOWLEDGED,
                request.user,
                request,
                details={"request_id": pk, "reasons": seen},
            )
            live_sessions.broadcast_live_session(call.pk)

        return Response(live_row_for(call))


def live_row_for(call):
    """The call's monitor row, read fresh from the database."""
    fresh = live_sessions.with_row_data(VideoSession.objects.filter(pk=call.pk)).get()
    return live_sessions.live_row(fresh)


class AdminAuditLogsView(APIView):
    """Recent platform activity for the Audit Logs page."""

    permission_classes = [IsAdminRole]

    def get(self, request):
        # Fetch the latest 50 audit logs
        logs = AuditLog.objects.all().order_by("-created_at")[:50]

        data = []
        for log in logs:
            # Format the event string to look nicer (e.g. "ADMIN_SIGN_IN" -> "Admin Sign In")
            formatted_action = log.event.replace("_", " ").title() if log.event else "Unknown Action"

            # Format the date
            timestamp = timezone.localtime(log.created_at).strftime("%b %d, %Y %H:%M")

            data.append({
                "id": str(log.id),
                "action": formatted_action,
                "meta": f"By {log.user.full_name if log.user else 'System'} • {timestamp}",
                "source": "admin" if log.user and log.user.role == "admin" else "system",
            })

        return Response(data)


class AdminLogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(
        required=True,
        help_text="The refresh token string received during authentication login.",
    )


class AdminLogoutView(APIView):
    """
    Logs out an administrator by blacklisting their refresh token and tracking it.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Dashboards"],
        summary="Administrator logout",
        request=AdminLogoutSerializer,
        responses={
            200: inline_serializer(
                "LogoutSuccess", {"detail": serializers.CharField()}
            ),
            400: inline_serializer("LogoutFailed", {"error": serializers.CharField()}),
        },
    )
    def post(self, request):
        serializer = AdminLogoutSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        refresh_token = serializer.validated_data["refresh"]

        try:
            token = RefreshToken(refresh_token)
            token.blacklist()

            if request.user.role == "admin":
                AuditLog.objects.create(
                    user=request.user,
                    event="ADMIN_SIGN_OUT",
                    ip_address=request.META.get("REMOTE_ADDR"),
                )

            return Response({"detail": "Successfully logged out."}, status=200)

        except TokenError:
            return Response({"error": "Invalid or expired token."}, status=400)
