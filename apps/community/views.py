from django.db.models import Q
from django.http import Http404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsDeafUser

from . import services
from .models import Connection
from .serializers import (
    ConnectionSerializer,
    PersonListItemSerializer,
    RequestSerializer,
    SendRequestResultSerializer,
    SendRequestSerializer,
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
        _pending(request, pk, as_addressee=False).delete()
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
