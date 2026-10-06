from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Custom user. Defined before the first migration so it can be extended
    in Sprint 1 (V2-066 role selection, V2-001 registration) without a
    painful migration later."""

    class Role(models.TextChoices):
        DEAF_USER = "deaf_user", "Deaf user"
        INTERPRETER = "interpreter", "Interpreter"
        INSTITUTION = "institution", "Institution"

    role = models.CharField(max_length=20, choices=Role.choices, blank=True)
