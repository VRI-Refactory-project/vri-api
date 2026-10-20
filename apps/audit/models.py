from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """A record of something an administrator or user did that the platform must be able to account for.

    Administrator sign-ins (V2-064), institution lifecycle changes, institution staff changes (V2-006),
    and interpreter requests and offers (V2-009, V2-010).
    Add an `Event` when another action needs recording. Entries are written by `apps.audit.services.record`
    and are read-only in the admin site.
    """

    class Event(models.TextChoices):
        ADMIN_SIGN_IN = "admin_sign_in", "Administrator signed in"
        INSTITUTION_CREATED = (
            "institution_created",
            "Administrator created an institution",
        )
        INSTITUTION_APPROVED = "institution_approved", "Institution approved"
        INSTITUTION_REJECTED = "institution_rejected", "Institution rejected"
        INSTITUTION_DEACTIVATED = "institution_deactivated", "Institution deactivated"
        STAFF_CREATED = "staff_created", "Institution staff account created"
        STAFF_UPDATED = "staff_updated", "Institution staff account changed"
        STAFF_REVOKED = "staff_revoked", "Institution staff access revoked"
        STAFF_REINSTATED = "staff_reinstated", "Institution staff access reinstated"
        REQUEST_CREATED = "request_created", "Interpreter request created"
        REQUEST_OFFERED = "request_offered", "Interpreter request offered to an interpreter"
        REQUEST_CANCELLED = "request_cancelled", "Interpreter request cancelled"
        OFFER_ACCEPTED = "offer_accepted", "Interpreter accepted a request"
        OFFER_DECLINED = "offer_declined", "Interpreter declined a request"
        OFFER_EXPIRED = "offer_expired", "Offer to an interpreter expired unanswered"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )
    # The account the action was done to, when that is not `user` (e.g. the staff member who was revoked).
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    event = models.CharField(
        max_length=40,
        choices=Event.choices,
        db_index=True,
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    # Extra facts about the action, e.g. {"request_id": 12, "reason": "REQUESTER_DISCONNECTED"}.
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.get_event_display()} - {self.user} ({self.created_at:%Y-%m-%d %H:%M})"
