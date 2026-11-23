from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.http import content_disposition_header
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsDeafUser

from . import services
from .models import Connection, Message, MessageFile, MessageImage
from .serializers import (
    ConnectionSerializer,
    ConversationSerializer,
    MessagePageSerializer,
    MessageSerializer,
    NewMessageSerializer,
    PersonListItemSerializer,
    RequestSerializer,
    SendRequestResultSerializer,
    SendRequestSerializer,
    message_data,
    person_data,
)

TAGS = ["Community"]
MAX_PEOPLE = 50


def _other(me, connection):
    return (
        connection.addressee
        if connection.requester_id == me.id
        else connection.requester
    )


def _with_people(queryset):
    return queryset.select_related(
        "requester__profile",
        "requester__photo",
        "addressee__profile",
        "addressee__photo",
    )


class PeopleView(APIView):
    permission_classes = [IsDeafUser]

    @extend_schema(
        tags=TAGS,
        summary="Find Deaf people to connect with",
        description=(
            "Active Deaf users other than you, by name, with how you stand with each. Shows at "
            f"most {MAX_PEOPLE}, so use `search` to narrow it. Phone numbers and emails are never shown."
        ),
        parameters=[
            OpenApiParameter("search", str, description="Part of a name or place."),
        ],
        responses=PersonListItemSerializer(many=True),
    )
    def get(self, request):
        people = (
            services.community_members()
            .exclude(pk=request.user.pk)
            .select_related("profile", "photo")
            .order_by("full_name", "id")
        )
        search = request.query_params.get("search", "").strip()
        if search:
            people = people.filter(
                Q(full_name__icontains=search) | Q(profile__location__icontains=search)
            )
        people = list(people[:MAX_PEOPLE])
        relationships = services.relationships_for(request.user, people)
        rows = [
            {**person_data(p), "relationship": relationships.get(p.id, services.NONE)}
            for p in people
        ]
        return Response(PersonListItemSerializer(rows, many=True).data)


class RequestListView(APIView):
    permission_classes = [IsDeafUser]

    @extend_schema(
        tags=TAGS,
        summary="Connection requests waiting for an answer",
        description="`direction=incoming` (default) are asks for you; `outgoing` are asks you sent.",
        parameters=[
            OpenApiParameter(
                "direction",
                str,
                enum=["incoming", "outgoing"],
                description="Which way.",
            ),
        ],
        responses=RequestSerializer(many=True),
    )
    def get(self, request):
        direction = request.query_params.get("direction", "incoming")
        if direction not in ("incoming", "outgoing"):
            raise ValidationError({"direction": "Use incoming or outgoing."})
        if direction == "incoming":
            records = Connection.objects.filter(
                addressee=request.user, status=Connection.Status.PENDING
            )
        else:
            records = Connection.objects.filter(
                requester=request.user, status=Connection.Status.PENDING
            )
        rows = [
            {
                "id": c.id,
                "person": person_data(_other(request.user, c)),
                "created_at": c.created_at,
            }
            for c in _with_people(records)
        ]
        return Response(RequestSerializer(rows, many=True).data)

    @extend_schema(
        tags=TAGS,
        summary="Ask someone to connect",
        description=(
            "If they already asked you, you are connected straight away "
            "(`relationship` is `connected`)."
        ),
        request=SendRequestSerializer,
        responses={201: SendRequestResultSerializer},
    )
    def post(self, request):
        serializer = SendRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target = (
            services.community_members()
            .filter(pk=serializer.validated_data["user"])
            .first()
        )
        if target is None:
            raise Http404
        try:
            connection, connected = services.send_request(request.user, target)
        except services.CannotConnect as error:
            raise ValidationError({"detail": str(error)}) from None
        return Response(
            {
                "id": connection.id,
                "relationship": services.CONNECTED
                if connected
                else services.REQUEST_SENT,
            },
            status=status.HTTP_201_CREATED,
        )


def _pending(request, pk, *, as_addressee):
    """A pending request that involves this person in the given role, or 404."""
    field = "addressee" if as_addressee else "requester"
    found = Connection.objects.filter(
        pk=pk, status=Connection.Status.PENDING, **{field: request.user}
    ).first()
    if found is None:
        raise Http404
    return found


class RequestDetailView(APIView):
    permission_classes = [IsDeafUser]

    @extend_schema(
        tags=TAGS,
        summary="Take back a request you sent",
        responses={204: None},
    )
    def delete(self, request, pk):
        services.withdraw(_pending(request, pk, as_addressee=False))
        return Response(status=status.HTTP_204_NO_CONTENT)


class RequestAcceptView(APIView):
    permission_classes = [IsDeafUser]

    @extend_schema(
        tags=TAGS,
        summary="Accept a connection request",
        request=None,
        responses={204: None},
    )
    def post(self, request, pk):
        services.accept(_pending(request, pk, as_addressee=True))
        return Response(status=status.HTTP_204_NO_CONTENT)


class RequestDeclineView(APIView):
    permission_classes = [IsDeafUser]

    @extend_schema(
        tags=TAGS,
        summary="Decline a connection request",
        request=None,
        responses={204: None},
    )
    def post(self, request, pk):
        services.decline(_pending(request, pk, as_addressee=True))
        return Response(status=status.HTTP_204_NO_CONTENT)


class ConnectionListView(APIView):
    permission_classes = [IsDeafUser]

    @extend_schema(
        tags=TAGS, summary="Your connections", responses=ConnectionSerializer(many=True)
    )
    def get(self, request):
        records = _with_people(
            Connection.objects.filter(
                Q(requester=request.user) | Q(addressee=request.user),
                status=Connection.Status.ACCEPTED,
            )
        ).order_by("-responded_at", "-id")
        rows = [
            {
                "id": c.id,
                "person": person_data(_other(request.user, c)),
                "connected_at": c.responded_at,
            }
            for c in records
        ]
        return Response(ConnectionSerializer(rows, many=True).data)


class ConnectionDetailView(APIView):
    permission_classes = [IsDeafUser]

    @extend_schema(tags=TAGS, summary="Remove a connection", responses={204: None})
    def delete(self, request, pk):
        connection = Connection.objects.filter(
            Q(requester=request.user) | Q(addressee=request.user),
            pk=pk,
            status=Connection.Status.ACCEPTED,
        ).first()
        if connection is None:
            raise Http404
        connection.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- Messages ---

DEFAULT_PAGE = 50
MAX_PAGE = 100


def _conversation(request, pk):
    connection = services.conversation_for(request.user, pk)
    if connection is None:
        raise Http404
    return connection


class ConversationListView(APIView):
    permission_classes = [IsDeafUser]

    @extend_schema(
        tags=TAGS,
        summary="Your conversations",
        description="One per connection, most recent activity first, with the unread count.",
        responses=ConversationSerializer(many=True),
    )
    def get(self, request):
        rows = [
            {
                "id": connection.id,
                "person": person_data(_other(request.user, connection)),
                "last_message": message_data(last, request.user) if last else None,
                "unread_count": unread,
            }
            for connection, last, unread in services.conversations_of(request.user)
        ]
        return Response(ConversationSerializer(rows, many=True).data)


class MessageListView(APIView):
    permission_classes = [IsDeafUser]
    # JSON for text and stickers; a multipart form when a photo goes with the message.
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @extend_schema(
        tags=TAGS,
        summary="Messages in a conversation",
        description=(
            f"The newest {DEFAULT_PAGE} messages, oldest first. When `has_more` is true, ask again "
            "with `before` set to the id of the oldest message you have."
        ),
        parameters=[
            OpenApiParameter(
                "before", int, description="Only messages with a lower id."
            ),
            OpenApiParameter("limit", int, description=f"How many, up to {MAX_PAGE}."),
        ],
        responses=MessagePageSerializer,
    )
    def get(self, request, pk):
        connection = _conversation(request, pk)
        try:
            before = request.query_params.get("before")
            before = int(before) if before else None
            limit = int(request.query_params.get("limit", DEFAULT_PAGE))
        except ValueError:
            raise ValidationError(
                {"detail": "before and limit must be numbers."}
            ) from None
        limit = max(1, min(limit, MAX_PAGE))
        messages, has_more = services.messages_in(connection, before, limit)
        return Response(
            {
                "messages": [message_data(m, request.user) for m in messages],
                "has_more": has_more,
            }
        )

    @extend_schema(
        tags=TAGS,
        summary="Send a message, a photo, a file or a sticker",
        description=(
            "Send JSON `{body}` or `{sticker}`, or a multipart form with `image` or `file` (and an "
            "optional `body` as its caption)."
        ),
        request=NewMessageSerializer,
        responses={201: MessageSerializer},
    )
    def post(self, request, pk):
        connection = _conversation(request, pk)
        serializer = NewMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = services.send_message(
            connection,
            request.user,
            body=serializer.validated_data.get("body", ""),
            sticker=serializer.validated_data.get("sticker", ""),
            image=serializer.validated_data.get("image"),
            file=serializer.validated_data.get("file"),
        )
        return Response(
            message_data(message, request.user), status=status.HTTP_201_CREATED
        )


class MessageDetailView(APIView):
    permission_classes = [IsDeafUser]

    @extend_schema(
        tags=TAGS,
        summary="Delete a message you sent, for both people",
        description=(
            "The text, sticker, photo and file are removed and the message stays in the chat marked "
            "`deleted`. Only the sender can do this. Deleting it again is fine."
        ),
        request=None,
        responses={200: MessageSerializer},
    )
    def delete(self, request, pk, message_id):
        connection = _conversation(request, pk)
        message = get_object_or_404(Message, pk=message_id, connection=connection)
        try:
            message = services.delete_message(connection, message, request.user)
        except services.CannotConnect as error:
            raise PermissionDenied(str(error)) from None
        return Response(message_data(message, request.user))


class MessageImageView(APIView):
    """The photo of one message. Only the two people in the conversation can open it."""

    permission_classes = [IsDeafUser]

    @extend_schema(
        tags=TAGS,
        summary="The photo of a message",
        responses={(200, "image/jpeg"): bytes},
    )
    def get(self, request, pk, message_id):
        connection = _conversation(request, pk)
        photo = (
            MessageImage.objects.filter(
                message_id=message_id, message__connection=connection
            )
            .only("data")
            .first()
        )
        if photo is None:
            raise Http404
        # The login middleware already marks every signed-in response "no-store", so the browser
        # does not keep the photo; the web app keeps it in memory while the page is open.
        return HttpResponse(bytes(photo.data), content_type="image/jpeg")


class MessageFileView(APIView):
    """The document of one message. Only the two people in the conversation can download it."""

    permission_classes = [IsDeafUser]

    @extend_schema(
        tags=TAGS,
        summary="The document of a message",
        responses={(200, "application/octet-stream"): bytes},
    )
    def get(self, request, pk, message_id):
        connection = _conversation(request, pk)
        attached = MessageFile.objects.filter(
            message_id=message_id, message__connection=connection
        ).first()
        if attached is None:
            raise Http404
        response = HttpResponse(
            bytes(attached.data), content_type=attached.content_type
        )
        # Always a download, never shown in the page, and the browser must not guess another type.
        response["Content-Disposition"] = content_disposition_header(
            True, attached.name
        )
        response["X-Content-Type-Options"] = "nosniff"
        return response


class ConversationReadView(APIView):
    permission_classes = [IsDeafUser]

    @extend_schema(
        tags=TAGS,
        summary="Mark the other person's messages as read",
        request=None,
        responses={204: None},
    )
    def post(self, request, pk):
        services.mark_read(_conversation(request, pk), request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
