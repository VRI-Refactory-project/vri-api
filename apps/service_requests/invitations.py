from django.conf import settings
from django.core.mail import send_mail

from apps.core.emailing import branded_html


def invitation_link(invitation):
    """The address the guest opens: the web app's /join/<token> page."""
    return f"{settings.WEB_APP_URL.rstrip('/')}/join/{invitation.token}"


def send_invitation_email(invitation, inviter):
    body = (
        "Hello,\n\n"
        f"{inviter.full_name} has invited you to join a video interpreting session on SignConnect.\n\n"
        "You do not need an account. Open this link when you are ready:\n\n"
        f"{invitation_link(invitation)}\n\n"
        "You can wait in the lobby until the interpreter arrives, then join with video or just listen in.\n"
        "The link stops working when the session ends.\n"
        "If you were not expecting this email, you can ignore it.\n"
    )
    send_mail(
        "You are invited to a SignConnect session",
        body,
        settings.DEFAULT_FROM_EMAIL,
        [invitation.email],
        html_message=branded_html(body),
    )
