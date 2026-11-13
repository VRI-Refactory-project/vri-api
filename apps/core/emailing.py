"""The standard SignConnect email.

Every system email keeps its plain-text body (so it still reads well in any mail app and in tests)
and also carries an HTML version in one consistent layout.

Two helpers:

    branded_html(body, highlight=None)
        The HTML version of an ordinary plain-text body (used by the approval, staff and
        interpreter emails). ``highlight`` is a code or temporary password shown in a large box.

    code_email_html(heading, greeting, intro, code, expires_text, safety_note)
        The designed layout for one-time-code emails (email verification and account recovery).

And one sender, so every system email leaves the same way:

    send_branded_email(subject, text, html, to_email, to_name)
        Brevo's API when BREVO_API_KEY is set, otherwise Django's configured email backend.
"""

import json
import logging
import urllib.error
import urllib.request
from email.utils import parseaddr
from html import escape

# The SignConnect palette, the same values as the web app (vri-web/src/all-css.css).
BRAND_PURPLE = "#382c77"  # --color-primary
PAGE_BG = "#f5f4fb"  # --color-bg-page
WHITE = "#ffffff"  # --color-bg-form
BORDER = "#cbd5e1"  # --color-border
TEXT = "#1e293b"  # --color-text-main
MUTED = "#64748b"  # --color-text-muted
FONT = "Manrope,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"

TAGLINE = "Video relay interpreting for Deaf and hard-of-hearing people in Uganda"

logger = logging.getLogger("apps.core.emailing")


def _logo_url():
    """Public https address of the white SignConnect wordmark (setting EMAIL_LOGO_URL).

    Email apps cannot show images that are not on the internet, so the file is served from the
    web app (vri-web/public/email-logo.png). When the setting is empty the header shows the
    name as text instead.
    """
    try:
        from django.conf import settings

        return getattr(settings, "EMAIL_LOGO_URL", "") or ""
    except Exception:  # settings not configured (for example in a plain script)
        return ""


def _header():
    url = _logo_url()
    if url:
        inner = (
            f'<img src="{escape(url, quote=True)}" alt="SignConnect" width="190" height="32" '
            'style="display:block;border:0;outline:none;text-decoration:none;height:auto;'
            'max-width:190px;">'
        )
    else:
        inner = "SignConnect"
    return (
        f'<tr><td style="background:{BRAND_PURPLE};padding:22px 28px;color:{WHITE};'
        f'font-size:20px;font-weight:700;letter-spacing:0.3px;">{inner}</td></tr>'
    )


def _paragraph(text):
    lines = "<br>".join(escape(line.strip()) for line in text.strip().splitlines() if line.strip())
    return (
        f'<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:{TEXT};">{lines}</p>'
    )


def _code_box(value):
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:8px 0 20px;">'
        "<tr>"
        f'<td style="background:{PAGE_BG};border:1px solid {BORDER};border-radius:8px;'
        f"padding:14px 24px;font-family:'Courier New',monospace;font-size:26px;"
        f'font-weight:700;letter-spacing:4px;color:{BRAND_PURPLE};">{escape(value)}</td>'
        "</tr></table>"
    )


def _content(body, highlight):
    parts = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        if highlight and block.strip() == highlight:
            parts.append(_code_box(highlight))
        elif highlight and highlight in block:
            before, after = block.split(highlight, 1)
            if before.strip():
                parts.append(_paragraph(before))
            parts.append(_code_box(highlight))
            if after.strip():
                parts.append(_paragraph(after))
        else:
            parts.append(_paragraph(block))
    return "".join(parts)


def _shell(inner, preheader=""):
    """The outer frame: purple header, white card, footer. Same for every email."""
    hidden = (
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:{PAGE_BG};">'
        f"{escape(preheader)}</div>"
        if preheader
        else ""
    )
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light"></head>'
        f'<body style="margin:0;padding:0;background:{PAGE_BG};font-family:{FONT};">{hidden}'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{PAGE_BG};padding:24px 12px;"><tr><td align="center">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="max-width:560px;background:{WHITE};border:1px solid {BORDER};border-radius:12px;'
        'overflow:hidden;">'
        f"{_header()}"
        f"{inner}"
        f'<tr><td style="padding:16px 28px 24px;border-top:1px solid {BORDER};'
        f'font-size:12px;line-height:1.5;color:{MUTED};">{escape(TAGLINE)}.<br>'
        "This message was sent automatically, so please do not reply to it.</td></tr>"
        "</table></td></tr></table></body></html>"
    )


def branded_html(body, highlight=None):
    """The HTML version of a plain-text email body."""
    inner = (
        f'<tr><td style="padding:28px 28px 12px;">{_content(body, highlight)}'
        f'<p style="margin:8px 0 0;font-size:15px;line-height:1.6;color:{TEXT};">'
        "Kind regards,<br>The SignConnect team</p></td></tr>"
    )
    return _shell(inner)


def code_email_html(heading, greeting, intro, code, expires_text, safety_note, preheader=""):
    """A one-time-code email: heading, short intro, big code, expiry, and a safety note."""
    inner = (
        f'<tr><td style="padding:30px 28px 8px;">'
        f'<h1 style="margin:0 0 18px;font-size:22px;line-height:1.3;color:{TEXT};">'
        f"{escape(heading)}</h1>"
        f"{_paragraph(greeting)}{_paragraph(intro)}"
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin:6px 0 10px;"><tr><td align="center" '
        f'style="background:{PAGE_BG};border:1px solid {BORDER};border-radius:10px;'
        f'padding:20px 12px;">'
        f'<div style="font-size:12px;letter-spacing:1px;text-transform:uppercase;'
        f'color:{MUTED};margin-bottom:8px;">Your code</div>'
        f"<div style=\"font-family:'Courier New',monospace;font-size:34px;font-weight:700;"
        f'letter-spacing:8px;color:{BRAND_PURPLE};">{escape(code)}</div>'
        "</td></tr></table>"
        f'<p style="margin:0 0 22px;font-size:13px;line-height:1.5;color:{MUTED};'
        f'text-align:center;">{escape(expires_text)}</p>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin:0 0 20px;"><tr><td style="border-left:4px solid {BRAND_PURPLE};'
        f'background:{PAGE_BG};padding:12px 14px;font-size:13px;line-height:1.55;color:{TEXT};">'
        f"{escape(safety_note)}</td></tr></table>"
        f'<p style="margin:0 0 8px;font-size:15px;line-height:1.6;color:{TEXT};">'
        "Kind regards,<br>The SignConnect team</p></td></tr>"
    )
    return _shell(inner, preheader or f"Your SignConnect code is {code}")


def send_branded_email(subject, text, html, to_email, to_name="", log=None):
    """Send one system email. Raises if it could not be sent.

    With BREVO_API_KEY set (production and staging) it goes out through Brevo's API. Without
    one (local development and tests) it uses Django's configured email backend, which prints
    to the console unless EMAIL_URL is set. Every SignConnect email should go through here: a
    plain ``send_mail`` call would only ever print to the log on a host that has a Brevo key
    but no SMTP settings.

    `log` is the logger for a Brevo failure (default: this module's), so callers keep their own
    log names. The key and the message are never logged.
    """
    from django.conf import settings
    from django.core.mail import send_mail

    log = log or logger
    brevo_api_key = getattr(settings, "BREVO_API_KEY", "")

    if not brevo_api_key:
        send_mail(
            subject,
            text,
            settings.DEFAULT_FROM_EMAIL,
            [to_email],
            fail_silently=False,
            html_message=html,
        )
        return

    # DEFAULT_FROM_EMAIL may look like "Sign Connect <a@b.com>". Brevo's sender.email must be
    # the plain address only, so split it into a name and an address first.
    sender_name, sender_email = parseaddr(settings.DEFAULT_FROM_EMAIL)
    if not sender_email:
        # Fail loudly with a clear reason instead of sending Brevo a request it will reject.
        log.error("DEFAULT_FROM_EMAIL has no valid email address in it")
        raise RuntimeError("DEFAULT_FROM_EMAIL is not a valid sender address")

    payload = {
        "sender": {"name": sender_name or "SignConnect", "email": sender_email},
        "to": [{"email": to_email, "name": to_name or to_email}],
        "subject": subject,
        "htmlContent": html,
        "textContent": text,
    }

    req = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=json.dumps(payload).encode("utf-8"),
    )
    req.add_header("accept", "application/json")
    req.add_header("api-key", brevo_api_key)
    req.add_header("content-type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except urllib.error.HTTPError as exc:
        # Brevo explains the failure in the response body (for example an unrecognised IP or
        # an unvalidated sender). Log it, but never the key or the message.
        detail = exc.read().decode("utf-8", "replace")[:300]
        log.error("Brevo API returned HTTP %s: %s", exc.code, detail)
        raise RuntimeError("Failed to send email via Brevo") from exc
    except urllib.error.URLError as exc:
        log.error("Could not reach Brevo: %s", exc.reason)
        raise RuntimeError("Failed to send email via Brevo") from exc
