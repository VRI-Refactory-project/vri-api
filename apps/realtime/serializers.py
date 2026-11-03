from rest_framework import serializers


class TicketSerializer(serializers.Serializer):
    """What an app needs to open a WebSocket."""

    ticket = serializers.CharField(help_text="Send as ?ticket= when opening the WebSocket.")
    expires_in = serializers.IntegerField(help_text="Seconds the ticket can be used for.")
