"""V2-045: admin decisions on an interpreter's verification.

Every decision runs in one transaction that locks the interpreter's profile, changes the status, and writes a
VerificationDecision row (the audit log). See docs/design/V2-045-interpreter-verification.md.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from rest_framework.exceptions import ValidationError

from .models import InterpreterProfile, VerificationDecision, VerificationStatus

logger = logging.getLogger("apps.interpreters.verification")

Action = VerificationDecision.Action
Status = VerificationStatus

# action -> (statuses it is allowed from, the resulting status, whether a reason is required)
TRANSITIONS = {
    Action.APPROVE: ({Status.INACTIVE, Status.REJECTED}, Status.APPROVED, False),
    Action.REJECT: ({Status.INACTIVE}, Status.REJECTED, True),
    # Deactivation also sets is_available = False in the same transaction, so the very next routing query
    # (services.get_eligible_interpreters) excludes the interpreter: "effective immediately".
    Action.DEACTIVATE: ({Status.APPROVED}, Status.DEACTIVATED, True),
    # Back to APPROVED; the interpreter switches themselves to available again.
    Action.REACTIVATE: ({Status.DEACTIVATED}, Status.APPROVED, False),
}

# Decisions the interpreter is emailed about (BE-05). Deactivation emails are not in the backlog.
NOTIFY_ACTIONS = {Action.APPROVE, Action.REJECT}


def decide(profile_id, action, admin, reason=""):
    """Apply `action` to the interpreter profile `profile_id` on behalf of `admin`.

    Returns the new VerificationDecision. Raises ValidationError (400) when the action is not allowed from the
    interpreter's current status, a required reason is missing, or approval is attempted without a certificate.
    """
    allowed_from, new_status, reason_required = TRANSITIONS[action]
    reason = (reason or "").strip()
    if reason_required and not reason:
        raise ValidationError({"reason": ["A reason is required."]})

    with transaction.atomic():
        profile = InterpreterProfile.objects.select_for_update(of=("self",)).select_related("user").get(pk=profile_id)
        previous_status = profile.verification_status
        if previous_status not in allowed_from:
            raise ValidationError({"detail": (
                f"Cannot {action} an interpreter whose status is {profile.get_verification_status_display()}."
            )})
        if action == Action.APPROVE and not profile.certifications.exists():
            raise ValidationError({"detail": "This interpreter has not uploaded a certificate yet."})

        profile.verification_status = new_status
        if new_status != Status.APPROVED:
            profile.is_available = False  # the database also enforces this (available_only_if_approved)
        profile.save(update_fields=["verification_status", "is_available", "updated_at"])

        if action == Action.REJECT:
            # The interpreter already sees this field on their latest certificate (GET /interpreters/me/).
            latest = profile.certifications.order_by("-uploaded_at", "-id").first()
            if latest is not None:
                latest.rejection_reason = reason
                latest.save(update_fields=["rejection_reason"])
        elif action == Action.APPROVE:
            profile.certifications.exclude(rejection_reason="").update(rejection_reason="")

        decision = VerificationDecision.objects.create(
            interpreter=profile,
            admin=admin,
            action=action,
            reason=reason,
            previous_status=previous_status,
            new_status=new_status,
        )
        if action in NOTIFY_ACTIONS:
            # Only email once the decision is safely saved; a rolled-back decision sends nothing.
            transaction.on_commit(lambda: notify_interpreter(decision))

    logger.warning("interpreter_verification_%s admin=%s interpreter=%s previous=%s new=%s",
                   action, admin.pk, profile.pk, previous_status, new_status)
    return decision


def notify_interpreter(decision):
    """Email the interpreter about an approval or rejection (BE-05, BE-06).

    A failed email is logged, never raised: the decision has already been saved and must stand.
    """
    user = decision.interpreter.user
    if not user.email:
        logger.warning("interpreter_verification_email_skipped reason=no_email interpreter=%s",
                       decision.interpreter_id)
        return
    if decision.action == Action.APPROVE:
        subject = "Your SignConnect interpreter account is approved"
        body = (
            f"Hello {user.full_name},\n\n"
            "Your interpreter certification has been reviewed and approved.\n"
            "Sign in to SignConnect and set yourself as available to start receiving interpreting requests.\n"
        )
    else:
        subject = "Your SignConnect interpreter verification was not approved"
        body = (
            f"Hello {user.full_name},\n\n"
            "Your interpreter certification has been reviewed and was not approved.\n\n"
            f"Reason: {decision.reason}\n\n"
            "If you have questions, reply to this email or contact SignConnect support.\n"
        )
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email])
    except Exception:  # SMTP down, bad address, ...
        logger.exception("interpreter_verification_email_failed interpreter=%s action=%s",
                         decision.interpreter_id, decision.action)
