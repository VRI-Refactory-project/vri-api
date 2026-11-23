from rest_framework import serializers

from apps.users.photos import InvalidPhoto, photo_path

from . import services
from .files import InvalidFile, process_chat_file
from .images import process_chat_image
from .stickers import STICKER_IDS

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


class FileSerializer(serializers.Serializer):
    name = serializers.CharField()
    size = serializers.IntegerField(help_text="Bytes.")
    url = serializers.CharField(help_text="Path to download it (fetch it signed in).")


class MessageSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    body = serializers.CharField(allow_blank=True, help_text="Empty for a sticker.")
    sticker = serializers.CharField(
        allow_blank=True, help_text="A sticker id, or empty for a text message."
    )
    image_url = serializers.CharField(
        allow_null=True,
        help_text="Path of the attached photo (fetch it signed in), or null.",
    )
    file = FileSerializer(allow_null=True, help_text="The attached document, or null.")
    created_at = serializers.DateTimeField()
    sent_by_me = serializers.BooleanField()
    read = serializers.BooleanField(
        help_text="The other person has opened it. Only meaningful for messages you sent."
    )
    deleted = serializers.BooleanField(
        help_text="The sender took it back. Show \"This message was deleted\"; there is nothing else in it."
    )


def _file_data(message):
    if not message.has_file:
        return None
    # `message.file` is one extra query per document; lists are short and documents are rare.
    attached = message.file
    return {
        "name": attached.name,
        "size": attached.size,
        "url": f"/api/v1/community/conversations/{message.connection_id}/messages/{message.id}/file/",
    }


def message_data(message, me):
    return {
        "id": message.id,
        "body": message.body,
        "sticker": message.sticker,
        "image_url": (
            f"/api/v1/community/conversations/{message.connection_id}/messages/{message.id}/image/"
            if message.has_image
            else None
        ),
        "file": _file_data(message),
        "created_at": message.created_at,
        "sent_by_me": message.sender_id == me.id,
        "read": message.read_at is not None,
        "deleted": message.deleted_at is not None,
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
    """Send text, a photo or a file (each with or without a caption) or a sticker. A sticker goes on its own."""

    body = serializers.CharField(max_length=2000, trim_whitespace=True, required=False)
    sticker = serializers.ChoiceField(choices=STICKER_IDS, required=False)
    image = serializers.FileField(
        required=False,
        help_text="A photo, sent as a multipart form. JPG, PNG, WebP or GIF, up to 8 MB.",
    )

    def validate_image(self, upload):
        try:
            return process_chat_image(upload)
        except InvalidPhoto as error:
            raise serializers.ValidationError(str(error)) from None

    file = serializers.FileField(
        required=False,
        help_text="A document, sent as a multipart form. PDF, Word, Excel, PowerPoint, TXT or CSV, up to 10 MB.",
    )

    def validate_file(self, upload):
        try:
            return process_chat_file(upload)
        except InvalidFile as error:
            raise serializers.ValidationError(str(error)) from None

    def validate(self, attrs):
        if attrs.get("sticker") and (
            attrs.get("body") or attrs.get("image") or attrs.get("file")
        ):
            raise serializers.ValidationError("Send a sticker on its own.")
        if attrs.get("image") and attrs.get("file"):
            raise serializers.ValidationError("Send a photo or a file, not both.")
        if not (
            attrs.get("body")
            or attrs.get("sticker")
            or attrs.get("image")
            or attrs.get("file")
        ):
            raise serializers.ValidationError(
                "Send a message, a photo, a file or a sticker."
            )
        return attrs
