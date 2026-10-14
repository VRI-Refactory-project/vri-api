from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.generics import RetrieveAPIView

from apps.core.permissions import IsApprovedInstitution, user_institution

from .serializers import InstitutionSerializer


@extend_schema(
    tags=["Institutions"],
    summary="My institution",
    responses={
        200: InstitutionSerializer,
        403: OpenApiResponse(description="Not an institution, or the institution is pending or rejected "
                                         "(code: institution_pending / institution_rejected)."),
    },
)
class MyInstitutionView(RetrieveAPIView):
    """The signed-in user's own institution. There is no way to ask for another institution by id."""

    permission_classes = [IsApprovedInstitution]
    serializer_class = InstitutionSerializer

    def get_object(self):
        return user_institution(self.request.user)
