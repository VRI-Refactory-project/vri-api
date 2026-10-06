from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from .utils import find_user_by_identifier


class PhoneOrEmailBackend(ModelBackend):
    """Lets users sign in with either their phone number or their email address.

    Used by the API login endpoint and by the Django admin login page.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        identifier = username or kwargs.get(get_user_model().USERNAME_FIELD)
        if identifier is None or password is None:
            return None
        user = find_user_by_identifier(identifier)
        if user is None:
            # Run the hasher anyway so response time does not reveal whether the account exists.
            get_user_model()().set_password(password)
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
