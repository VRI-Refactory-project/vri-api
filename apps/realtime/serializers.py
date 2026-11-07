from rest_framework import serializers


class TicketSerializer(serializers.Serializer):
    """What an app needs to open a WebSocket."""

    ticket = serializers.CharField(
        help_text="Send as ?ticket= when opening the WebSocket."
    )
    expires_in = serializers.IntegerField(
        help_text="Seconds the ticket can be used for."
    )


class GuestInvitationCreateSerializer(serializers.Serializer):
    """Optional name for the invited hearing participant."""

    guest_name = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
        default="",
    )


class GuestInvitationSerializer(serializers.Serializer):
    """Invitation details returned to the authenticated creator."""

    invitation_url = serializers.URLField()
    expires_at = serializers.DateTimeField()


class GuestJoinSerializer(serializers.Serializer):
    """LiveKit credentials returned after a guest invitation is validated."""

    token = serializers.CharField()
    url = serializers.CharField()
    room = serializers.CharField()


class GuestJoinRequestSerializer(serializers.Serializer):
    """Invitation token submitted by an unauthenticated guest."""

    token = serializers.CharField(
        write_only=True,
        trim_whitespace=True,
    )
