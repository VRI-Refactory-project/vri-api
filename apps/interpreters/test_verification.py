import shutil
import tempfile
from datetime import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import (
    CertificationDocument,
    InterpreterProfile,
    VerificationDecision,
    VerificationStatus,
)

User = get_user_model()
PASSWORD = "Sign-Connect-2026"
# A Wednesday at 10:00. Routing also depends on working hours (default Mon-Fri 08:00-17:00),
# so these tests must not depend on the time of day they happen to run.
WORKING_HOURS = timezone.make_aware(datetime(2026, 10, 14, 10, 0))


def make_interpreter(phone="0772123456", status=VerificationStatus.INACTIVE, **profile_fields):
    user = User.objects.create_user(
        phone, PASSWORD, full_name="Test Interpreter", email=f"i{phone}@example.com", role=User.Role.INTERPRETER
    )
    return InterpreterProfile.objects.create(user=user, verification_status=status, **profile_fields)


def make_admin(phone="0700000100"):
    return User.objects.create_superuser(phone, PASSWORD, full_name="Admin User", email=f"a{phone}@example.com")


class VerificationModelTests(TestCase):
    def test_deactivated_interpreter_cannot_be_available(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_interpreter(status=VerificationStatus.DEACTIVATED, is_available=True)

    def test_decisions_are_listed_newest_first(self):
        profile, admin = make_interpreter(), make_admin()
        first = VerificationDecision.objects.create(
            interpreter=profile, admin=admin, action=VerificationDecision.Action.REJECT, reason="Blurry scan",
            previous_status=VerificationStatus.INACTIVE, new_status=VerificationStatus.REJECTED,
        )
        second = VerificationDecision.objects.create(
            interpreter=profile, admin=admin, action=VerificationDecision.Action.APPROVE,
            previous_status=VerificationStatus.REJECTED, new_status=VerificationStatus.APPROVED,
        )
        self.assertEqual(list(profile.decisions.all()), [second, first])

    def test_audit_history_survives_user_deletion_attempts(self):
        profile, admin = make_interpreter(), make_admin()
        VerificationDecision.objects.create(
            interpreter=profile, admin=admin, action=VerificationDecision.Action.APPROVE,
            previous_status=VerificationStatus.INACTIVE, new_status=VerificationStatus.APPROVED,
        )
        with self.assertRaises(ProtectedError):
            admin.delete()
        with self.assertRaises(ProtectedError):
            profile.user.delete()
        self.assertEqual(VerificationDecision.objects.count(), 1)


class TempMediaMixin:
    """Keeps uploaded test files out of the project's media folder."""

    def use_temp_media(self):
        media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media_root, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media_root)
        override.enable()
        self.addCleanup(override.disable)


def add_certificate(profile, content=b"%PDF-1.4 test"):
    return CertificationDocument.objects.create(
        interpreter=profile,
        document=SimpleUploadedFile("cert.pdf", content, content_type="application/pdf"),
        document_name="cert.pdf",
        certificate_type="Sign Language Interpreter",
        issuing_organisation="UNAD",
    )


class ManageReviewApiTests(TempMediaMixin, APITestCase):
    def setUp(self):
        self.use_temp_media()
        self.admin = make_admin()
        self.pending = make_interpreter("0772000001")
        self.approved = make_interpreter("0772000002", status=VerificationStatus.APPROVED)
        self.cert = add_certificate(self.pending)
        self.list_url = reverse("manage-interpreters:list")

    def as_role(self, role):
        user = User.objects.create_user("0701999999", PASSWORD, full_name="Someone", role=role)
        self.client.force_authenticate(user)

    def test_requires_sign_in(self):
        for url in (self.list_url, reverse("manage-interpreters:detail", args=[self.pending.pk]),
                    reverse("manage-interpreters:certification-file", args=[self.pending.pk, self.cert.pk])):
            self.assertEqual(self.client.get(url).status_code, 401, url)

    def test_other_roles_are_forbidden(self):
        for role in (User.Role.DEAF_HOH, User.Role.INTERPRETER, User.Role.INSTITUTION):
            User.objects.filter(phone_number="+256701999999").delete()
            self.as_role(role)
            self.assertEqual(self.client.get(self.list_url).status_code, 403, role)
            file_url = reverse("manage-interpreters:certification-file", args=[self.pending.pk, self.cert.pk])
            self.assertEqual(self.client.get(file_url).status_code, 403, role)

    def test_queue_lists_interpreters_oldest_first_and_filters_by_status(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.data], [self.pending.pk, self.approved.pk])

        response = self.client.get(self.list_url, {"status": "INACTIVE"})
        self.assertEqual([row["id"] for row in response.data], [self.pending.pk])
        self.assertEqual(response.data[0]["certification_count"], 1)
        self.assertEqual(response.data[0]["phone_number"], "+256772000001")

    def test_invalid_status_filter_is_rejected(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(self.list_url, {"status": "PENDING"}).status_code, 400)

    def test_queue_excludes_profiles_of_other_roles(self):
        deaf_user = User.objects.create_user("0701000003", PASSWORD, full_name="Deaf User", role=User.Role.DEAF_HOH)
        stray = InterpreterProfile.objects.create(user=deaf_user)
        self.client.force_authenticate(self.admin)
        ids = [row["id"] for row in self.client.get(self.list_url).data]
        self.assertNotIn(stray.pk, ids)
        detail = reverse("manage-interpreters:detail", args=[stray.pk])
        self.assertEqual(self.client.get(detail).status_code, 404)

    def test_detail_shows_certificates_with_protected_link_and_history(self):
        VerificationDecision.objects.create(
            interpreter=self.pending, admin=self.admin, action=VerificationDecision.Action.REJECT, reason="Blurry",
            previous_status=VerificationStatus.INACTIVE, new_status=VerificationStatus.REJECTED,
        )
        self.client.force_authenticate(self.admin)
        response = self.client.get(reverse("manage-interpreters:detail", args=[self.pending.pk]))
        self.assertEqual(response.status_code, 200)
        cert = response.data["certifications"][0]
        self.assertTrue(cert["file_url"].endswith(f"/api/v1/manage/interpreters/{self.pending.pk}"
                                                  f"/certifications/{self.cert.pk}/file/"))
        self.assertNotIn("document", cert)
        self.assertEqual(response.data["decisions"][0]["reason"], "Blurry")
        self.assertEqual(response.data["decisions"][0]["admin_name"], "Admin User")

    def test_admin_downloads_certificate(self):
        self.client.force_authenticate(self.admin)
        url = reverse("manage-interpreters:certification-file", args=[self.pending.pk, self.cert.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4 test")

    def test_certificate_must_belong_to_that_interpreter(self):
        self.client.force_authenticate(self.admin)
        url = reverse("manage-interpreters:certification-file", args=[self.approved.pk, self.cert.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_missing_file_is_404(self):
        self.cert.document.delete(save=False)
        self.client.force_authenticate(self.admin)
        url = reverse("manage-interpreters:certification-file", args=[self.pending.pk, self.cert.pk])
        self.assertEqual(self.client.get(url).status_code, 404)


class DecisionApiMixin(TempMediaMixin):
    def setUp(self):
        self.use_temp_media()
        self.admin = make_admin()
        self.client.force_authenticate(self.admin)

    def act(self, action, profile, reason=None):
        data = {} if reason is None else {"reason": reason}
        url = reverse(f"manage-interpreters:{action}", args=[profile.pk])
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(url, data, format="json")


class ApproveRejectApiTests(DecisionApiMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.profile = make_interpreter("0772000011")
        self.cert = add_certificate(self.profile)

    def test_requires_admin(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.act("approve", self.profile).status_code, 401)
        interpreter = self.profile.user
        self.client.force_authenticate(interpreter)
        self.assertEqual(self.act("approve", self.profile).status_code, 403)  # cannot approve themselves
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.verification_status, VerificationStatus.INACTIVE)

    def test_approve_pending_interpreter(self):
        response = self.act("approve", self.profile)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["verification_status"], "APPROVED")
        decision = response.data["decisions"][0]
        self.assertEqual((decision["action"], decision["previous_status"], decision["new_status"]),
                         ("approve", "INACTIVE", "APPROVED"))
        self.assertEqual(decision["admin"], self.admin.pk)

    def test_approved_interpreter_becomes_routable_once_available(self):
        from .services import get_eligible_interpreters

        self.act("approve", self.profile)
        self.profile.refresh_from_db()
        self.assertNotIn(self.profile, get_eligible_interpreters(now=WORKING_HOURS))  # approved but not yet available
        self.profile.is_available = True
        self.profile.save()
        self.assertIn(self.profile, get_eligible_interpreters(now=WORKING_HOURS))

    def test_approve_needs_a_certificate(self):
        no_cert = make_interpreter("0772000012")
        response = self.act("approve", no_cert)
        self.assertEqual(response.status_code, 400)
        self.assertIn("certificate", str(response.data["detail"]))
        self.assertFalse(VerificationDecision.objects.exists())

    def test_reject_needs_a_reason(self):
        for body in (None, "", "   "):
            response = self.act("reject", self.profile, body)
            self.assertEqual(response.status_code, 400, body)
            self.assertIn("reason", response.data)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.verification_status, VerificationStatus.INACTIVE)

    def test_reject_records_reason_on_decision_and_latest_certificate(self):
        response = self.act("reject", self.profile, "The certificate scan is unreadable.")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["verification_status"], "REJECTED")
        decision = VerificationDecision.objects.get()
        self.assertEqual((decision.action, decision.reason, decision.admin),
                         ("reject", "The certificate scan is unreadable.", self.admin))
        self.cert.refresh_from_db()
        self.assertEqual(self.cert.rejection_reason, "The certificate scan is unreadable.")

    def test_rejected_interpreter_can_be_approved_later_and_old_reason_is_cleared(self):
        self.act("reject", self.profile, "Expired certificate.")
        response = self.act("approve", self.profile, "New certificate checked.")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["verification_status"], "APPROVED")
        self.cert.refresh_from_db()
        self.assertEqual(self.cert.rejection_reason, "")
        self.assertEqual([d.action for d in self.profile.decisions.all()], ["approve", "reject"])

    def test_invalid_transitions_change_nothing(self):
        approved = make_interpreter("0772000013", status=VerificationStatus.APPROVED)
        add_certificate(approved)
        self.assertEqual(self.act("approve", approved).status_code, 400)
        self.assertEqual(self.act("reject", approved, "Too late").status_code, 400)
        approved.refresh_from_db()
        self.assertEqual(approved.verification_status, VerificationStatus.APPROVED)
        self.assertFalse(VerificationDecision.objects.exists())

    def test_unknown_or_non_interpreter_profile_is_404(self):
        self.assertEqual(self.client.post(reverse("manage-interpreters:approve", args=[99999])).status_code, 404)
        deaf_user = User.objects.create_user("0701000004", PASSWORD, full_name="Deaf User", role=User.Role.DEAF_HOH)
        stray = InterpreterProfile.objects.create(user=deaf_user)
        add_certificate(stray)
        self.assertEqual(self.act("approve", stray).status_code, 404)


class DeactivateReactivateApiTests(DecisionApiMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.profile = make_interpreter("0772000021", status=VerificationStatus.APPROVED, is_available=True)
        add_certificate(self.profile)

    def test_deactivation_stops_new_requests_immediately(self):
        from .services import get_eligible_interpreters

        self.assertIn(self.profile, get_eligible_interpreters(now=WORKING_HOURS))
        response = self.act("deactivate", self.profile, "Certificate lapsed in September.")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["verification_status"], "DEACTIVATED")
        self.assertFalse(response.data["is_available"])
        self.assertNotIn(self.profile, get_eligible_interpreters(now=WORKING_HOURS))

    def test_deactivation_needs_a_reason_and_is_audited(self):
        self.assertEqual(self.act("deactivate", self.profile).status_code, 400)
        self.act("deactivate", self.profile, "Complaint under review.")
        decision = VerificationDecision.objects.get()
        self.assertEqual((decision.action, decision.previous_status, decision.new_status, decision.reason),
                         ("deactivate", "APPROVED", "DEACTIVATED", "Complaint under review."))

    def test_deactivated_interpreter_can_still_sign_in_but_cannot_go_available(self):
        self.act("deactivate", self.profile, "Certificate lapsed.")
        self.client.force_authenticate(None)
        login = self.client.post(reverse("auth-login"), {"identifier": "0772000021", "password": PASSWORD},
                                 format="json")
        self.assertEqual(login.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        me = self.client.patch(reverse("interpreters:interpreter-profile-detail"), {"is_available": True},
                               format="json")
        self.assertEqual(me.status_code, 400)

    def test_reactivate_returns_to_approved_but_not_available(self):
        self.act("deactivate", self.profile, "Certificate lapsed.")
        response = self.act("reactivate", self.profile, "Renewed certificate received.")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["verification_status"], "APPROVED")
        self.assertFalse(response.data["is_available"])
        self.assertEqual([d["action"] for d in response.data["decisions"]], ["reactivate", "deactivate"])

    def test_only_approved_can_be_deactivated_and_only_deactivated_reactivated(self):
        pending = make_interpreter("0772000022")
        self.assertEqual(self.act("deactivate", pending, "x").status_code, 400)
        self.assertEqual(self.act("reactivate", self.profile).status_code, 400)
        self.assertFalse(VerificationDecision.objects.exists())

    def test_requires_admin(self):
        self.client.force_authenticate(self.profile.user)
        self.assertEqual(self.act("deactivate", self.profile, "x").status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.act("reactivate", self.profile).status_code, 401)


class VerificationEmailTests(DecisionApiMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.profile = make_interpreter("0772000031")
        add_certificate(self.profile)

    def test_approval_email(self):
        self.act("approve", self.profile)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, [self.profile.user.email])
        self.assertIn("approved", email.subject)

    def test_rejection_email_includes_the_reason(self):
        self.act("reject", self.profile, "Please upload a certificate issued by UNAD.")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("not approved", mail.outbox[0].subject)
        self.assertIn("Reason: Please upload a certificate issued by UNAD.", mail.outbox[0].body)

    def test_no_email_for_failed_or_unnotified_decisions(self):
        self.act("reject", self.profile)  # missing reason -> 400
        self.assertEqual(len(mail.outbox), 0)
        approved = make_interpreter("0772000032", status=VerificationStatus.APPROVED)
        self.act("deactivate", approved, "Certificate lapsed.")  # not in the backlog's notifications
        self.assertEqual(len(mail.outbox), 0)

    def test_email_failure_does_not_undo_the_decision(self):
        with mock.patch("apps.interpreters.verification.send_mail", side_effect=OSError("smtp down")), \
                self.assertLogs("apps.interpreters.verification", level="ERROR"):
            response = self.act("approve", self.profile)
        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.verification_status, VerificationStatus.APPROVED)
