import json
import logging
import urllib.error
import urllib.request
from email.utils import parseaddr

from django.conf import settings
from django.core.mail import send_mail

from apps.core.emailing import branded_html

logger = logging.getLogger("apps.service_requests.invitations")


def invitation_link(invitation):
    """The address the guest opens: the web app's /join/<token> page."""
    return f"{settings.WEB_APP_URL.rstrip('/')}/join/{invitation.token}"


def _send_with_brevo(to_email, subject, text, html):
    """Same route as the recovery codes: Brevo's API, used when BREVO_API_KEY is set."""
    # DEFAULT_FROM_EMAIL may look like "Sign Connect <a@b.com>"; Brevo wants the plain address.
    sender_name, sender_email = parseaddr(settings.DEFAULT_FROM_EMAIL)
    if not sender_email:
        logger.error("DEFAULT_FROM_EMAIL has no valid email address in it")
        raise RuntimeError("DEFAULT_FROM_EMAIL is not a valid sender address")

    payload = {
        "sender": {"name": sender_name or "SignConnect", "email": sender_email},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html,
        "textContent": text,
    }
    req = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=json.dumps(payload).encode("utf-8"),
    )
    req.add_header("accept", "application/json")
    req.add_header("api-key", settings.BREVO_API_KEY)
    req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except urllib.error.HTTPError as exc:
        # Brevo explains the failure in the body (for example an unvalidated sender). Never log the key.
        detail = exc.read().decode("utf-8", "replace")[:300]
        logger.error("Brevo API returned HTTP %s: %s", exc.code, detail)
        raise RuntimeError("Failed to send email via Brevo") from exc
    except urllib.error.URLError as exc:
        logger.error("Could not reach Brevo: %s", exc.reason)
        raise RuntimeError("Failed to send email via Brevo") from exc


def send_invitation_email(invitation, inviter):
    subject = "You are invited to a SignConnect session"
    body = (
        "Hello,\n\n"
        f"{inviter.full_name} has invited you to join a video interpreting session on SignConnect.\n\n"
        "You do not need an account. Open this link when you are ready:\n\n"
        f"{invitation_link(invitation)}\n\n"
        "You can wait in the lobby until the interpreter arrives, then join with video or just listen in.\n"
        "The link stops working when the session ends.\n"
        "If you were not expecting this email, you can ignore it.\n"
    )
    html = branded_html(body)

    if getattr(settings, "BREVO_API_KEY", ""):
        _send_with_brevo(invitation.email, subject, body, html)
        return

    # Local development and tests: use Django's configured email backend.
    send_mail(
        subject,
        body,
        settings.DEFAULT_FROM_EMAIL,
        [invitation.email],
        fail_silently=False,
        html_message=html,
    )
