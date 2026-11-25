from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import generics, serializers
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import IsAdminRole, IsDeafUser, IsInstitution, IsInterpreter, user_staff_membership

from .models import SupportRequest


class SupportRequestCreateSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(read_only=True)
    status = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    subject = serializers.CharField(min_length=3, max_length=200, trim_whitespace=True)
    category = serializers.CharField(min_length=1, max_length=50, trim_whitespace=True)
    message = serializers.CharField(min_length=10, max_length=5000, trim_whitespace=True)

    class Meta:
        model = SupportRequest
        fields = ("id", "subject", "category", "message", "status", "created_at")


class SupportRequestAdminSerializer(serializers.ModelSerializer):
    requester = serializers.SerializerMethodField()

    class Meta:
        model = SupportRequest
        fields = (
            "id", "subject", "category", "message", "status", "admin_response", "follow_up",
            "created_at", "updated_at", "requester",
        )

    def get_requester(self, obj):
        result = {
            "user_id": obj.requester_id,
            "name": obj.requester_name,
            "role": obj.requester_role,
        }
        if obj.requester_institution_name:
            result["institution_name"] = obj.requester_institution_name
            result["institution_role"] = obj.requester_institution_role
        return result


class MySupportRequestSerializer(serializers.ModelSerializer):
    """Only the requester-safe fields, including the response written by an administrator."""

    class Meta:
        model = SupportRequest
        fields = (
            "id",
            "subject",
            "category",
            "message",
            "status",
            "admin_response",
            "created_at",
            "updated_at",
        )


class SupportRequestAdminUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupportRequest
        fields = ("status", "admin_response", "follow_up")
        extra_kwargs = {
            "status": {"required": False},
            "admin_response": {"required": False, "allow_blank": True},
            "follow_up": {"required": False, "allow_blank": True},
        }


class SupportRequestPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class SupportRequestCreateView(generics.CreateAPIView):
    serializer_class = SupportRequestCreateSerializer
    permission_classes = [IsAuthenticated, IsDeafUser | IsInterpreter | IsInstitution]

    @extend_schema(
        tags=["Support"],
        summary="Create a support request",
        responses={201: SupportRequestCreateSerializer},
    )
    def perform_create(self, serializer):
        user = self.request.user
        values = {"requester": user, "requester_name": user.full_name, "requester_role": user.role}
        if user.role == "institution":
            membership = user_staff_membership(user)
            if membership is None or not membership.is_active:
                raise ValidationError({"detail": "An active institution membership is required."})
            values.update(
                requester_institution_name=membership.institution.organisation_name,
                requester_institution_role=membership.role,
            )
        serializer.save(**values)


class MySupportRequestListView(generics.ListAPIView):
    serializer_class = MySupportRequestSerializer
    permission_classes = [IsAuthenticated, IsDeafUser | IsInterpreter | IsInstitution]
    pagination_class = SupportRequestPagination

    @extend_schema(
        tags=["Support"],
        summary="List my support requests",
        responses={200: MySupportRequestSerializer(many=True)},
    )
    def get_queryset(self):
        return SupportRequest.objects.filter(requester=self.request.user).order_by(
            "-created_at", "-id"
        )


class AdminSupportRequestListView(generics.ListAPIView):
    serializer_class = SupportRequestAdminSerializer
    permission_classes = [IsAuthenticated, IsAdminRole]
    pagination_class = SupportRequestPagination

    @extend_schema(
        tags=["Administration"], summary="List support requests",
        parameters=[inline_serializer("SupportRequestListQuery", {
            "status": serializers.ChoiceField(choices=("OPEN", "IN_PROGRESS", "RESOLVED", "all"), required=False),
            "page": serializers.IntegerField(required=False),
            "page_size": serializers.IntegerField(required=False),
        })],
        responses={200: SupportRequestAdminSerializer(many=True)},
    )
    def get_queryset(self):
        queryset = SupportRequest.objects.select_related("requester").order_by("-created_at", "-id")
        status_filter = self.request.query_params.get("status")
        if status_filter and status_filter.lower() != "all":
            valid = SupportRequest.Status.values
            if status_filter not in valid:
                raise ValidationError({"status": [f"Choose one of: {', '.join(valid)}, or all."]})
            queryset = queryset.filter(status=status_filter)
        return queryset


class AdminSupportRequestDetailView(generics.RetrieveUpdateAPIView):
    queryset = SupportRequest.objects.select_related("requester")
    serializer_class = SupportRequestAdminUpdateSerializer
    permission_classes = [IsAuthenticated, IsAdminRole]
    http_method_names = ["patch", "head", "options"]

    @extend_schema(
        tags=["Administration"],
        summary="Update a support request",
        responses={200: SupportRequestAdminSerializer},
    )
    def patch(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = SupportRequestAdminUpdateSerializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(SupportRequestAdminSerializer(instance).data)
