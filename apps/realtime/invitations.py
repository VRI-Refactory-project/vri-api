
"""Secure guest invitations for VRI video sessions."""

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from livekit import api

from apps.realtime.models import GuestInvitation, VideoSession
from apps.service_requests.call_tokens import room_name
from apps.service_requests.models import ServiceRequest


def create_guest_invitation(service_request, user, guest_name=""):
    """Create a one-time-secret invitation for an accepted video session."""

    if service_request.status != ServiceRequest.Status.ASSIGNED:
        raise ValueError("Only assigned sessions can have guest invitations.")

    if user.pk not in {
        service_request.requester_id,
        service_request.interpreter.user_id if service_request.interpreter else None,
    }:
        raise PermissionError("You are not allowed to invite guests to this session.")

    if not settings.LIVEKIT_API_KEY or not settings.LIVEKIT_API_SECRET:
        raise RuntimeError("LiveKit credentials are not configured.")

    video_session, _ = VideoSession.objects.get_or_create(
        service_request=service_request,
    )

    if video_session.status == VideoSession.Status.ENDED:
        raise ValueError("This video session has already ended.")

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    expires_at = timezone.now() + timedelta(
        seconds=settings.GUEST_INVITATION_EXPIRY_SECONDS
    )

    invitation = GuestInvitation.objects.create(
        video_session=video_session,
        created_by=user,
        token_hash=token_hash,
        guest_name=guest_name,
        expires_at=expires_at,
    )

    invitation_url = (
        f"{settings.FRONTEND_URL.rstrip('/')}/guest/join/"
        f"{raw_token}"
    )

    return invitation, invitation_url


def join_guest_invitation(raw_token):
    """Validate a guest invitation and issue a room-scoped LiveKit token."""

    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    try:
        invitation = GuestInvitation.objects.select_related(
            "video_session__service_request"
        ).get(token_hash=token_hash)
    except GuestInvitation.DoesNotExist:
        raise ValueError("This invitation is invalid or unavailable.")

    now = timezone.now()

    if invitation.revoked_at is not None:
        raise ValueError("This invitation is invalid or unavailable.")

    if invitation.expires_at <= now:
        raise ValueError("This invitation is invalid or unavailable.")

    video_session = invitation.video_session

    if video_session.status == VideoSession.Status.ENDED:
        raise ValueError("This video session has already ended.")

    if not settings.LIVEKIT_API_KEY or not settings.LIVEKIT_API_SECRET:
        raise RuntimeError("LiveKit credentials are not configured.")

    remaining_seconds = int((invitation.expires_at - now).total_seconds())

    if remaining_seconds <= 0:
        raise ValueError("This invitation is invalid or unavailable.")

    service_request = video_session.service_request

    grants = api.VideoGrants(
        room_join=True,
        room=room_name(service_request),
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
    )

    guest_token = (
        api.AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
        .with_identity(f"guest-{invitation.guest_identity}")
        .with_name(invitation.guest_name or "Guest")
        .with_ttl(timedelta(seconds=remaining_seconds))
        .with_grants(grants)
        .to_jwt()
    )

    return {
        "token": guest_token,
        "url": settings.LIVEKIT_URL,
        "room": room_name(service_request),
    }
