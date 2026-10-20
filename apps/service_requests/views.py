
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import IsDeafUser
from apps.service_requests.serializers import ServiceRequestSerializer
from apps.service_requests.services import create_and_assign_request


class ServiceRequestListCreateView(generics.ListCreateAPIView):
    serializer_class = ServiceRequestSerializer
    permission_classes = [IsAuthenticated, IsDeafUser]

    def get_queryset(self):
        return (
            self.request.user.service_requests
            .select_related("requester", "interpreter", "interpreter__user")
            .all()
        )

    def perform_create(self, serializer):
        service_request = create_and_assign_request(
            requester=self.request.user,
            specialisation_required=serializer.validated_data[
                "specialisation_required"
            ],
            description=serializer.validated_data.get("description", ""),
        )
        self.created_request = service_request

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        response.data = ServiceRequestSerializer(
            self.created_request
        ).data
        return response
