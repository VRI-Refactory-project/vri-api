from rest_framework import serializers

from apps.users.photos import photo_path

from . import services

RELATIONSHIPS = [
    services.NONE,
    services.REQUEST_SENT,
    services.REQUEST_RECEIVED,
    services.CONNECTED,
]


def _location(user):
    profile = getattr(user, "profile", None)
    return profile.location if profile else ""


def person_data(user):
    """What one Deaf user may see of another: a name, a place and a picture. Never a phone or email."""
    return {
        "id": user.id,
        "name": user.full_name,
        "location": _location(user),
        "photo_url": photo_path(user),
    }


class PersonSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    location = serializers.CharField(allow_blank=True)
    photo_url = serializers.CharField(allow_null=True)


class PersonListItemSerializer(PersonSerializer):
    relationship = serializers.ChoiceField(choices=RELATIONSHIPS)


class SendRequestSerializer(serializers.Serializer):
    user = serializers.IntegerField(help_text="The id of the person to ask.")


class RequestSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    person = PersonSerializer(help_text="The other person in the request.")
    created_at = serializers.DateTimeField()


class SendRequestResultSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    relationship = serializers.ChoiceField(choices=RELATIONSHIPS)


class ConnectionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    person = PersonSerializer()
    connected_at = serializers.DateTimeField()


class MessageSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    body = serializers.CharField()
    created_at = serializers.DateTimeField()
    sent_by_me = serializers.BooleanField()
    read = serializers.BooleanField(
        help_text="The other person has opened it. Only meaningful for messages you sent."
    )


def message_data(message, me):
    return {
        "id": message.id,
        "body": message.body,
        "created_at": message.created_at,
        "sent_by_me": message.sender_id == me.id,
        "read": message.read_at is not None,
    }


class ConversationSerializer(serializers.Serializer):
    id = serializers.IntegerField(
        help_text="The connection id. Use it in the message URLs."
    )
    person = PersonSerializer()
    last_message = MessageSerializer(allow_null=True)
    unread_count = serializers.IntegerField()


class MessagePageSerializer(serializers.Serializer):
    messages = MessageSerializer(many=True, help_text="Oldest first.")
    has_more = serializers.BooleanField(
        help_text="There are older messages: ask again with `before`."
    )


class NewMessageSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=2000, trim_whitespace=True)
