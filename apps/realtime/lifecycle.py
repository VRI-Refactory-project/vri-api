
"""Process LiveKit events and maintain the actual video-session lifecycle."""

from django.db import transaction
from django.utils import timezone

from apps.realtime.events import broadcast_video_session_status
from apps.realtime.models import VideoSession
from apps.service_requests.models import SessionInvitation


def process_livekit_event(event):
    """Process participant and room events received from LiveKit."""
    event_type = event.event
    room_name = event.room.name if event.room else ""

    # Only process events for rooms created for VRI service requests.
    if not room_name.startswith("session-"):
        return

    try:
        request_id = int(room_name.removeprefix("session-"))
    except ValueError:
        return

    participant = event.participant
    identity = participant.identity if participant else ""

    with transaction.atomic():
        video_session = (
            VideoSession.objects.select_for_update(of=("self",))
            .select_related(
                "service_request__requester",
                "service_request__interpreter__user",
            )
            .filter(service_request_id=request_id)
            .first()
        )

        # Ignore events when no video session exists or the call has ended.
        if video_session is None or video_session.status == VideoSession.Status.ENDED:
            return

        service_request = video_session.service_request
        participants = list(video_session.joined_participants or [])
        now = timezone.now()

        # A finished LiveKit room marks the actual video call as ended.
        if event_type == "room_finished":
            video_session.status = VideoSession.Status.ENDED
            video_session.ended_at = video_session.ended_at or now
            service_request.ended_at = service_request.ended_at or now

            video_session.save(
                update_fields=["status", "ended_at", "updated_at"]
            )
            service_request.save(
                update_fields=["ended_at", "updated_at"]
            )

            broadcast_video_session_status(video_session)
            return

        # Participant events must include an identity.
        if not identity:
            return

        if event_type == "participant_joined":
            _handle_participant_joined(
                identity=identity,
                participants=participants,
                video_session=video_session,
                service_request=service_request,
                now=now,
            )

        elif event_type == "participant_left":
            participants = [
                item for item in participants if item != identity
            ]

        else:
            # Ignore events unrelated to participant presence or room lifecycle.
            return

        new_status = _get_video_session_status(
            participants=participants,
            service_request=service_request,
        )

        if new_status == VideoSession.Status.CONNECTED:
            video_session.connected_at = video_session.connected_at or now

        changed = (
            participants != video_session.joined_participants
            or new_status != video_session.status
        )

        video_session.joined_participants = participants
        video_session.status = new_status

        if changed:
            video_session.save(
                update_fields=[
                    "joined_participants",
                    "status",
                    "requester_joined_at",
                    "interpreter_joined_at",
                    "connected_at",
                    "updated_at",
                ]
            )
            broadcast_video_session_status(video_session)


def _handle_participant_joined(
    identity,
    participants,
    video_session,
    service_request,
    now,
):
    """Record a participant joining the LiveKit room."""
    if identity not in participants:
        participants.append(identity)

    if identity == f"user-{service_request.requester_id}":
        video_session.requester_joined_at = (
            video_session.requester_joined_at or now
        )

    interpreter = service_request.interpreter
    if interpreter and identity == f"user-{interpreter.user_id}":
        video_session.interpreter_joined_at = (
            video_session.interpreter_joined_at or now
        )

    if identity.startswith("guest-"):
        _record_guest_join(
            identity=identity,
            service_request=service_request,
            now=now,
        )


def _record_guest_join(identity, service_request, now):
    """Record the first join time for a valid guest invitation."""
    try:
        invitation_id = int(identity.removeprefix("guest-"))
    except ValueError:
        return

    SessionInvitation.objects.filter(
        pk=invitation_id,
        service_request=service_request,
        joined_at__isnull=True,
    ).update(joined_at=now)


def _get_video_session_status(participants, service_request):
    """Determine call status using only the requester and assigned interpreter."""
    requester_identity = f"user-{service_request.requester_id}"

    interpreter = service_request.interpreter
    interpreter_identity = (
        f"user-{interpreter.user_id}" if interpreter else None
    )

    requester_joined = requester_identity in participants
    interpreter_joined = (
        interpreter_identity in participants
        if interpreter_identity
        else False
    )

    if requester_joined and interpreter_joined:
        return VideoSession.Status.CONNECTED

    if requester_joined or interpreter_joined:
        return VideoSession.Status.CONNECTING

    return VideoSession.Status.WAITING
