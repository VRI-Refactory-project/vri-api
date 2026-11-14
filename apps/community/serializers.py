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
