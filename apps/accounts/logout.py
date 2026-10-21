from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken


class LogoutView(APIView):
    """Ends a session on the server: the refresh token stops working, so it cannot be used to get
    new access tokens even if someone copied it.

    Needs only the refresh token, so signing out still works after the access token has expired.
    An unknown or already-used token gets the same answer, so nothing is revealed about it.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=["Auth"],
        summary="Sign out (revoke the refresh token)",
        request=inline_serializer("LogoutRequest", {"refresh": serializers.CharField()}),
        responses={200: OpenApiResponse(description="Signed out")},
    )
    def post(self, request):
        token = request.data.get("refresh")
        if isinstance(token, str) and token:
            try:
                RefreshToken(token).blacklist()
            except TokenError:
                pass  # expired, already blacklisted or not a token: nothing left to revoke
        return Response({"detail": "Signed out."}, status=status.HTTP_200_OK)
