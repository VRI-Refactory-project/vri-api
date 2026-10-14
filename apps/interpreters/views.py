from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .availability import (
    monday_of,
    month_exceptions,
    unavailable_reason,
    week_schedule,
)
from .models import DateException, InterpreterProfile, WeeklyAvailability
from .serializers import (
    AvailabilityStatusSerializer,
    AvailabilityStatusUpdateSerializer,
    CertificationDocumentSerializer,
    DayScheduleSerializer,
    DayUpdateSerializer,
    InterpreterProfileSerializer,
    MonthExceptionsSerializer,
    MonthQuerySerializer,
    WeekQuerySerializer,
    WeekScheduleSerializer,
)


@extend_schema(
    tags=["Interpreter Profile Management"],
    summary="Upload interpreter certification document",
    description=(
        "Upload a PDF or image certification document for administrative "
        "verification and onboarding (V2-003)."
    ),
)
class CertificationUploadView(generics.CreateAPIView):
    permission_classes = (permissions.IsAuthenticated,)
    parser_classes = (MultiPartParser, FormParser)
    serializer_class = CertificationDocumentSerializer

    def perform_create(self, serializer):
        profile, _ = InterpreterProfile.objects.get_or_create(user=self.request.user)
        uploaded_file = self.request.FILES.get("document")
        document_name = (
            uploaded_file.name if uploaded_file else "Certification Document"
        )
        serializer.save(interpreter=profile, document_name=document_name)


@extend_schema(
    tags=["Interpreter Profile Management"],
    summary="Get or update current interpreter profile",
    description=(
        "Retrieve or update profile details for the authenticated interpreter (V2-068).\n\n"
        "**Fields supported:**\n"
        "- `specialisation`: Dropdown choice (`GENERAL`, `MEDICAL`, `LEGAL`, `OTHER`).\n"
        "- `specialisation_other`: Custom text required when `specialisation` is set to `OTHER`.\n"
        "- `sign_languages`: List of sign language preferences (e.g. `['Ugandan Sign Language']`).\n"
        "- `is_available`: Boolean status toggle (requires `APPROVED` verification status).\n"
        "- `working_hours_schedule`: JSON schedule definition."
    ),
)
class InterpreterProfileDetailView(generics.RetrieveUpdateAPIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = InterpreterProfileSerializer

    def get_object(self):
        profile, _ = InterpreterProfile.objects.get_or_create(user=self.request.user)
        return profile


# ---- V2-021 availability schedule (new) ----


def get_interpreter_profile(user):
    """The signed-in interpreter's profile. Other roles have no schedule."""
    if user.role != get_user_model().Role.INTERPRETER:
        raise PermissionDenied("Only interpreters have an availability schedule.")
    profile, _ = InterpreterProfile.objects.get_or_create(user=user)
    return profile


def parse_editable_date(value):
    """A YYYY-MM-DD date from the URL. Past dates cannot be changed."""
    try:
        the_date = date.fromisoformat(value)
    except ValueError:
        raise ValidationError({"date": "Use the format YYYY-MM-DD."}) from None
    if the_date < timezone.localdate():
        raise ValidationError(
            {"date": "You cannot change availability for a past date."}
        )
    return the_date


@extend_schema(
    tags=["Interpreter Availability"],
    summary="Get the availability schedule for one week",
    description=(
        "Returns the 7 days (Monday to Sunday) of the week that contains week_start. "
        "Defaults to the current week. Each day says whether its hours come from the "
        "default week ('default') or from a change for that one date ('exception')."
    ),
    parameters=[WeekQuerySerializer],
    responses=WeekScheduleSerializer,
)
class WeeklyScheduleView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        profile = get_interpreter_profile(request.user)

        query = WeekQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)

        any_day = query.validated_data.get("week_start") or timezone.localdate()
        week_start = monday_of(any_day)

        data = {
            "week_start": week_start,
            "week_end": week_start + timedelta(days=6),
            "days": week_schedule(profile, week_start),
        }
        return Response(WeekScheduleSerializer(data).data)


@extend_schema(
    tags=["Interpreter Availability"],
    summary="Save the availability for one day",
    description=(
        "Saves one day from the schedule screen. The date is in the URL (YYYY-MM-DD).\n\n"
        "- `repeat_weekly: false` (default): changes only that date.\n"
        "- `repeat_weekly: true`: changes that weekday in the default week, so every "
        "future week uses it (a change saved earlier for this one date is removed).\n"
        "- `is_available: false` means unavailable all day (times are ignored).\n\n"
        "Past dates cannot be changed. The change applies to the very next routing decision."
    ),
    request=DayUpdateSerializer,
    responses=DayScheduleSerializer,
)
class DayAvailabilityView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def patch(self, request, day):
        profile = get_interpreter_profile(request.user)

        the_date = parse_editable_date(day)

        serializer = DayUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        values = {
            "is_available": data["is_available"],
            "start_time": data["start_time"],
            "end_time": data["end_time"],
        }

        with transaction.atomic():
            if data["repeat_weekly"]:
                # Change the default week for this weekday ...
                WeeklyAvailability.objects.update_or_create(
                    interpreter=profile,
                    weekday=the_date.weekday(),
                    defaults=values,
                )
                # ... and drop any one-off change for this date, which would
                # otherwise hide the new default.
                DateException.objects.filter(
                    interpreter=profile, date=the_date
                ).delete()
            else:
                # Change only this date.
                DateException.objects.update_or_create(
                    interpreter=profile,
                    date=the_date,
                    defaults=values,
                )

        updated_day = week_schedule(profile, monday_of(the_date))[the_date.weekday()]
        return Response(DayScheduleSerializer(updated_day).data)


def status_payload(profile):
    """The dashboard status for this interpreter, using the same rule as routing."""
    reason = unavailable_reason(profile)
    return AvailabilityStatusSerializer(
        {
            "is_available": profile.is_available,
            "available_now": reason is None,
            "reason": reason,
        }
    ).data


class AvailabilityStatusView(APIView):
    """The dashboard control (MOB-01 to MOB-04): read the status, flip the switch."""

    permission_classes = (permissions.IsAuthenticated,)

    @extend_schema(
        tags=["Interpreter Availability"],
        summary="Get the real-time availability status",
        description=(
            "is_available is the dashboard switch. available_now says whether she can "
            "be offered a request at this very moment (switch AND schedule), and reason "
            "says why not: not_approved, switched_off, day_off or outside_hours. "
            "Poll this to keep the dashboard indicator correct."
        ),
        responses=AvailabilityStatusSerializer,
    )
    def get(self, request):
        profile = get_interpreter_profile(request.user)
        return Response(status_payload(profile))

    @extend_schema(
        tags=["Interpreter Availability"],
        summary="Switch availability on or off",
        description=(
            "Sets the dashboard switch. Turning it on needs an APPROVED profile. "
            "The change applies to the very next routing decision."
        ),
        request=AvailabilityStatusUpdateSerializer,
        responses=AvailabilityStatusSerializer,
    )
    def patch(self, request):
        profile = get_interpreter_profile(request.user)

        serializer = AvailabilityStatusUpdateSerializer(
            data=request.data, context={"profile": profile}
        )
        serializer.is_valid(raise_exception=True)

        profile.is_available = serializer.validated_data["is_available"]
        profile.save(update_fields=["is_available", "updated_at"])
        return Response(status_payload(profile))


@extend_schema(
    tags=["Interpreter Availability"],
    summary="List the one-off date changes for a month",
    description=(
        "The dots on the Exceptions calendar. month is YYYY-MM (default: this month). "
        "kind is 'unavailable' (unavailable all day) or 'modified_hours'. "
        "To add or edit a date, use PATCH .../days/<date>/ with repeat_weekly false."
    ),
    parameters=[MonthQuerySerializer],
    responses=MonthExceptionsSerializer,
)
class ExceptionListView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        profile = get_interpreter_profile(request.user)

        query = MonthQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)

        month = query.validated_data.get("month")
        if month:
            year, number = (int(part) for part in month.split("-"))
            first_day = date(year, number, 1)
        else:
            first_day = timezone.localdate().replace(day=1)

        data = {
            "month": first_day.strftime("%Y-%m"),
            "exceptions": month_exceptions(profile, first_day),
        }
        return Response(MonthExceptionsSerializer(data).data)


@extend_schema(
    tags=["Interpreter Availability"],
    summary="Remove the change for one date",
    description=(
        "The Remove link on the Exceptions card. That date goes back to the default "
        "week. Past dates cannot be changed. Returns 404 if no change is saved for "
        "that date."
    ),
    responses={204: None},
)
class ExceptionDetailView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def delete(self, request, day):
        profile = get_interpreter_profile(request.user)
        the_date = parse_editable_date(day)

        deleted, _ = DateException.objects.filter(
            interpreter=profile, date=the_date
        ).delete()
        if not deleted:
            raise NotFound("No change is saved for this date.")
        return Response(status=204)
