from datetime import time, timedelta

from django.utils import timezone

from .models import DateException, VerificationStatus, WeeklyAvailability

# Used when an interpreter has not saved a row for a weekday yet.
# Placeholder default: Monday to Friday, 08:00 to 17:00. Confirm with the team.
DEFAULT_START = time(8, 0)
DEFAULT_END = time(17, 0)


def default_hours(weekday):
    """Default hours for a weekday (Monday = 0) when nothing is saved."""
    if weekday <= 4:  # Monday to Friday
        return True, DEFAULT_START, DEFAULT_END
    return False, None, None


def monday_of(day):
    """The Monday of the week that contains `day`."""
    return day - timedelta(days=day.weekday())


def hours_for(interpreter, day):
    """Return (is_available, start_time, end_time) for ONE date.

    Order: a date exception wins, then the saved default week,
    then the built-in default week.
    """
    exception = DateException.objects.filter(interpreter=interpreter, date=day).first()
    if exception:
        return exception.is_available, exception.start_time, exception.end_time

    weekly = WeeklyAvailability.objects.filter(
        interpreter=interpreter,
        weekday=day.weekday(),  # Monday = 0, same as WeeklyAvailability.weekday
    ).first()
    if weekly:
        return weekly.is_available, weekly.start_time, weekly.end_time

    return default_hours(day.weekday())


def unavailable_reason(interpreter, now=None):
    """Why this interpreter cannot be offered a request right now.

    Returns None when she CAN be offered one. Otherwise one of:
      "not_approved"  - her profile is not approved yet
      "switched_off"  - she switched herself off on the dashboard
      "day_off"       - today is a day off (date exception or default week)
      "outside_hours" - today is a working day but it is outside her hours

    This is the single rule. Routing and the dashboard status both use it,
    so they can never disagree.
    """
    now = now or timezone.localtime()  # uses TIME_ZONE (should be Africa/Kampala)

    if interpreter.verification_status != VerificationStatus.APPROVED:
        return "not_approved"

    # The dashboard switch beats the schedule.
    if not interpreter.is_available:
        return "switched_off"

    # Otherwise look at today's hours (date exception, then default week).
    available, start, end = hours_for(interpreter, now.date())
    if not available or start is None or end is None:
        return "day_off"

    if not start <= now.time() <= end:
        return "outside_hours"

    return None


def is_available_now(interpreter, now=None):
    """True only if this interpreter can be offered a request at this moment."""
    return unavailable_reason(interpreter, now) is None


def week_schedule(interpreter, week_start):
    """The 7 days of one week, for the schedule screen.

    Each day says where its hours came from:
    "exception" = a change for that one date, "default" = the normal week.
    Uses two queries for the whole week, not one per day.
    """
    days = [week_start + timedelta(days=i) for i in range(7)]
    exceptions = {
        e.date: e
        for e in DateException.objects.filter(interpreter=interpreter, date__in=days)
    }
    weekly = {
        w.weekday: w for w in WeeklyAvailability.objects.filter(interpreter=interpreter)
    }

    result = []
    for day in days:
        if day in exceptions:
            row = exceptions[day]
            available, start, end, source = (
                row.is_available,
                row.start_time,
                row.end_time,
                "exception",
            )
        elif day.weekday() in weekly:
            row = weekly[day.weekday()]
            available, start, end, source = (
                row.is_available,
                row.start_time,
                row.end_time,
                "default",
            )
        else:
            available, start, end = default_hours(day.weekday())
            source = "default"

        result.append(
            {
                "date": day,
                "weekday": day.weekday(),
                "is_available": available,
                "start_time": start,
                "end_time": end,
                "source": source,
            }
        )
    return result


def month_exceptions(interpreter, first_day):
    """The one-off dates in the month that starts on first_day (the calendar dots).

    kind: "unavailable" = unavailable all day, "modified_hours" = different hours.
    """
    next_month = (first_day + timedelta(days=32)).replace(day=1)
    rows = DateException.objects.filter(
        interpreter=interpreter, date__gte=first_day, date__lt=next_month
    )
    return [
        {
            "date": row.date,
            "is_available": row.is_available,
            "start_time": row.start_time,
            "end_time": row.end_time,
            "kind": "modified_hours" if row.is_available else "unavailable",
        }
        for row in rows
    ]
