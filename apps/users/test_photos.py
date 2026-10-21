import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image
from rest_framework.test import APIClient

from apps.users.models import ProfilePhoto

User = get_user_model()
PASSWORD = "Sign-Connect-2026"
UPLOAD_URL = "/api/v1/auth/me/photo/"


def picture(fmt="PNG", size=(800, 600), name="me.png"):
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 30, 90)).save(buffer, format=fmt)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class ProfilePhotoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "0700000500", PASSWORD, full_name="Maria", role=User.Role.DEAF_HOH
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def upload(self, file):
        return self.client.post(UPLOAD_URL, {"photo": file}, format="multipart")

    def test_requires_sign_in(self):
        self.assertEqual(APIClient().post(UPLOAD_URL).status_code, 401)

    def test_upload_stores_a_small_square_jpeg_and_returns_its_link(self):
        response = self.upload(picture())

        self.assertEqual(response.status_code, 200)
        link = response.json()["profile_photo"]
        self.assertTrue(link.startswith("/api/v1/auth/photos/"))
        image = Image.open(io.BytesIO(bytes(ProfilePhoto.objects.get(user=self.user).image)))
        self.assertEqual(image.format, "JPEG")
        self.assertEqual(image.size, (400, 400))

    def test_the_link_shows_the_picture_without_signing_in(self):
        link = self.upload(picture()).json()["profile_photo"]

        response = APIClient().get(link)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")

    def test_a_new_upload_replaces_the_old_one_and_changes_the_link(self):
        first = self.upload(picture()).json()["profile_photo"]
        second = self.upload(picture()).json()["profile_photo"]

        self.assertNotEqual(first, second)
        self.assertEqual(ProfilePhoto.objects.filter(user=self.user).count(), 1)
        self.assertEqual(APIClient().get(first).status_code, 404)

    def test_get_returns_the_current_link_or_none(self):
        self.assertIsNone(self.client.get(UPLOAD_URL).json()["profile_photo"])
        link = self.upload(picture()).json()["profile_photo"]
        self.assertEqual(self.client.get(UPLOAD_URL).json()["profile_photo"], link)

    def test_remove_deletes_the_photo(self):
        link = self.upload(picture()).json()["profile_photo"]

        self.assertEqual(self.client.delete(UPLOAD_URL).status_code, 204)

        self.assertIsNone(self.client.get(UPLOAD_URL).json()["profile_photo"])
        self.assertEqual(APIClient().get(link).status_code, 404)

    def test_rejects_a_file_that_is_not_a_picture(self):
        fake = SimpleUploadedFile("me.png", b"not a picture", content_type="image/png")

        response = self.upload(fake)

        self.assertEqual(response.status_code, 400)
        self.assertIn("photo", response.json())
        self.assertFalse(ProfilePhoto.objects.exists())

    def test_rejects_a_picture_over_five_megabytes(self):
        big = SimpleUploadedFile("big.png", b"0" * (5 * 1024 * 1024 + 1), content_type="image/png")

        self.assertEqual(self.upload(big).status_code, 400)

    def test_rejects_a_request_with_no_file(self):
        self.assertEqual(self.client.post(UPLOAD_URL, {}, format="multipart").status_code, 400)

    def test_the_deaf_profile_includes_the_photo_link(self):
        link = self.upload(picture()).json()["profile_photo"]

        data = self.client.get("/api/v1/auth/deaf-user/me/").json()

        self.assertEqual(data["profile_photo"], link)

    def test_the_old_deaf_profile_path_still_works(self):
        response = self.client.post(
            "/api/v1/auth/deaf-user/me/photo/", {"photo": picture()}, format="multipart"
        )
        self.assertEqual(response.status_code, 200)

    def test_an_unknown_link_is_not_found(self):
        self.assertEqual(APIClient().get("/api/v1/auth/photos/nope/").status_code, 404)
