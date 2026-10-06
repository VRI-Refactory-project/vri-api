from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView

from apps.accounts.serializers import TokenPairSerializer

from .permissions import ROLE_PERMISSIONS, IsAdminRole, IsDeafUser, IsInstitution, IsInterpreter

User = get_user_model()
PASSWORD = "Sign-Connect-2026"


def make_user(role, phone="0772123456"):
    return User.objects.create_user(phone, PASSWORD, full_name="Test User", role=role)


def protected_view(*permission_classes):
    """A throwaway view guarded by the given permission classes."""

    class ProtectedView(APIView):
        def get(self, request):
            return Response({"ok": True})

    ProtectedView.permission_classes = list(permission_classes)
    return ProtectedView.as_view()


class RolePermissionTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def get(self, view, user=None, **headers):
        request = self.factory.get("/protected/", **headers)
        if user is not None:
            force_authenticate(request, user=user)
        return view(request)

    def test_not_signed_in_gets_401(self):
        for permission in ROLE_PERMISSIONS.values():
            self.assertEqual(self.get(protected_view(permission)).status_code, 401, permission.__name__)

    def test_matching_role_gets_200(self):
        for i, (role, permission) in enumerate(ROLE_PERMISSIONS.items()):
            user = make_user(role, phone=f"070000003{i}")
            self.assertEqual(self.get(protected_view(permission), user).status_code, 200, role)

    def test_other_roles_get_403(self):
        users = {role: make_user(role, phone=f"070000004{i}") for i, role in enumerate(ROLE_PERMISSIONS)}
        for role, permission in ROLE_PERMISSIONS.items():
            for other_role, user in users.items():
                if other_role == role:
                    continue
                response = self.get(protected_view(permission), user)
                self.assertEqual(response.status_code, 403, f"{other_role} on {permission.__name__}")
                self.assertEqual(str(response.data["detail"]), IsDeafUser.message)

    def test_superuser_is_checked_by_role_like_everyone_else(self):
        admin = User.objects.create_superuser("0700000001", PASSWORD, full_name="Admin")
        self.assertEqual(self.get(protected_view(IsAdminRole), admin).status_code, 200)
        self.assertEqual(self.get(protected_view(IsInterpreter), admin).status_code, 403)

    def test_roles_can_be_combined(self):
        view = protected_view(IsDeafUser | IsInstitution)
        self.assertEqual(self.get(view, make_user(User.Role.DEAF_HOH, "0700000051")).status_code, 200)
        self.assertEqual(self.get(view, make_user(User.Role.INSTITUTION, "0700000052")).status_code, 200)
        self.assertEqual(self.get(view, make_user(User.Role.INTERPRETER, "0700000053")).status_code, 403)

    def test_role_is_read_from_database_not_token(self):
        user = make_user(User.Role.INTERPRETER)
        access = TokenPairSerializer.for_user(user)["access"]
        view = protected_view(IsInterpreter)
        self.assertEqual(self.get(view, HTTP_AUTHORIZATION=f"Bearer {access}").status_code, 200)

        user.role = User.Role.DEAF_HOH
        user.save()
        self.assertEqual(self.get(view, HTTP_AUTHORIZATION=f"Bearer {access}").status_code, 403)

    def test_deactivated_user_token_is_rejected(self):
        user = make_user(User.Role.INTERPRETER)
        access = TokenPairSerializer.for_user(user)["access"]
        user.is_active = False
        user.save()
        response = self.get(protected_view(IsInterpreter), HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertEqual(response.status_code, 401)
