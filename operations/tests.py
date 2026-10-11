from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, connections, transaction
from django.test import Client, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from agreements.models import ExchangeAgreement
from agreements.services import create_exchange_agreement
from audit.models import BusinessEvent
from contracts.services import create_contract
from suppliers.models import Supplier
from . import services
from .forms import ValidationResponseForm
from .models import GoodsReceipt, Requirement, ValidationRequest, CaseDocument, CaseEmail, SupplierInvoice


class CaseOperationsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_roles", verbosity=0)
        cls.manager = User.objects.create_user("operations-manager", password="test-password")
        cls.bookkeeper = User.objects.create_user("operations-bookkeeper", password="test-password")
        cls.manager.groups.add(Group.objects.get(name="Manager"))
        cls.bookkeeper.groups.add(Group.objects.get(name="Bookkeeper"))
        cls.supplier = Supplier.objects.create(reference="OPS", name="Operations Supplier")
        cls.contract = create_contract(actor=cls.manager, supplier=cls.supplier, reference="OPS-C", title="Materials", start_date=date(2026, 1, 1))
        cls.agreement = create_exchange_agreement(actor=cls.manager, supplier=cls.supplier, contract=cls.contract)
        cls.other = create_exchange_agreement(actor=cls.manager, supplier=cls.supplier, contract=cls.contract)

    def receipt(self, **changes):
        data = dict(actor=self.bookkeeper, agreement_id=self.agreement.pk, description="Steel brackets", quantity=Decimal("10.000"),
                    unit="units", reception_date=date(2026, 10, 1))
        data.update(changes)
        return services.declare_goods_received(**data)

    def requirement(self, **changes):
        data = dict(actor=self.bookkeeper, agreement_id=self.agreement.pk, note="Await delivery information")
        data.update(changes)
        return services.create_requirement(**data)

    def validation(self, **changes):
        data = dict(actor=self.bookkeeper, agreement_id=self.agreement.pk, person="Sarah Smith", email="sarah@example.test",
                    question="Are these goods acceptable?", request_date=date(2026, 10, 1))
        data.update(changes)
        return services.request_validation(**data)

    def respond(self, validation, **changes):
        data = dict(actor=self.manager, agreement_id=self.agreement.pk, validation_id=validation.pk,
                    decision="APPROVED", response_date=date(2026, 10, 2), responder="Sarah Smith", response_comment="Reviewed evidence")
        data.update(changes)
        return services.record_validation(**data)

    @staticmethod
    def upload(name="evidence.pdf"):
        return SimpleUploadedFile(name, b"test evidence", content_type="application/pdf")

    def test_both_roles_can_operate_and_events_identify_actor(self):
        for actor in (self.manager, self.bookkeeper):
            receipt = self.receipt(actor=actor)
            event = self.agreement.events.get(event_type="GOODS_RECEIVED", related_object_id=receipt.pk)
            self.assertEqual(event.actor_user, actor)
            self.assertEqual(event.metadata["after"]["description"], "Steel brackets")

    def test_unauthorized_users_cannot_operate(self):
        outsider = User.objects.create_user("operations-outsider")
        inactive = User.objects.create_user("operations-inactive", is_active=False)
        inactive.groups.add(Group.objects.get(name="Manager"))
        for actor in (AnonymousUser(), outsider, inactive):
            with self.assertRaises(PermissionDenied):
                self.receipt(actor=actor)
        self.assertEqual(GoodsReceipt.objects.count(), 0)

    def test_closed_case_rejects_every_operation_without_events(self):
        receipt = self.receipt()
        requirement = self.requirement()
        validation = self.validation()
        ExchangeAgreement.objects.filter(pk=self.agreement.pk).update(status="CLOSED")
        actions = [
            lambda: self.receipt(), lambda: self.requirement(), lambda: self.validation(),
            lambda: services.assess_goods_receipt(actor=self.manager, agreement_id=self.agreement.pk, receipt_id=receipt.pk, valid_goods_percentage=100),
            lambda: services.update_requirement(actor=self.manager, agreement_id=self.agreement.pk, requirement_id=requirement.pk, note="Updated"),
            lambda: services.complete_requirement(actor=self.manager, agreement_id=self.agreement.pk, requirement_id=requirement.pk),
            lambda: services.cancel_requirement(actor=self.manager, agreement_id=self.agreement.pk, requirement_id=requirement.pk),
            lambda: self.respond(validation),
            lambda: services.cancel_validation(actor=self.manager, agreement_id=self.agreement.pk, validation_id=validation.pk),
            lambda: services.send_validation_request(actor=self.manager, agreement_id=self.agreement.pk, validation_id=validation.pk),
            lambda: services.attach_document(actor=self.manager, agreement_id=self.agreement.pk, title="Evidence", category="Inspection", date=date(2026, 10, 1), document=self.upload()),
            lambda: services.attach_supplier_invoice(actor=self.manager, agreement_id=self.agreement.pk, reference="INV", amount=10, currency="EUR", invoice_date=date(2026, 10, 1), document=self.upload()),
            lambda: services.attach_email(actor=self.manager, agreement_id=self.agreement.pk, sender="a@example.test", recipients="b@example.test", subject="Evidence", occurred_at=timezone.now(), direction="RECEIVED", body="Text"),
        ]
        events_before = BusinessEvent.objects.count()
        for action in actions:
            with self.assertRaises(ValidationError):
                action()
        self.assertEqual(BusinessEvent.objects.count(), events_before)

    def test_receipt_starts_unassessed_and_accepts_zero_valid_goods(self):
        receipt = self.receipt()
        self.assertFalse(receipt.is_validated)
        self.assertIsNone(receipt.valid_goods_percentage)
        services.assess_goods_receipt(actor=self.bookkeeper, agreement_id=self.agreement.pk, receipt_id=receipt.pk, valid_goods_percentage=0)
        receipt.refresh_from_db()
        self.assertTrue(receipt.is_validated)
        self.assertEqual(receipt.valid_goods_percentage, 0)
        self.assertEqual(receipt.assessed_by, self.bookkeeper)

    def test_assessment_range_and_quantity_validation(self):
        for quantity in (0, -1):
            with self.assertRaises(ValidationError):
                self.receipt(quantity=quantity)
        receipt = self.receipt()
        for percentage in (-1, 101, None):
            with self.assertRaises(ValidationError):
                services.assess_goods_receipt(actor=self.manager, agreement_id=self.agreement.pk, receipt_id=receipt.pk, valid_goods_percentage=percentage)
        receipt.refresh_from_db()
        self.assertFalse(receipt.is_validated)

    def test_database_enforces_receipt_assessment_consistency(self):
        receipt = self.receipt()
        with self.assertRaises(IntegrityError), transaction.atomic():
            GoodsReceipt.objects.filter(pk=receipt.pk).update(is_validated=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            GoodsReceipt.objects.filter(pk=receipt.pk).update(quantity=0)

    def test_assessment_correction_requires_note_and_keeps_previous_value(self):
        receipt = self.receipt()
        services.assess_goods_receipt(actor=self.manager, agreement_id=self.agreement.pk, receipt_id=receipt.pk, valid_goods_percentage=80)
        with self.assertRaises(ValidationError):
            services.assess_goods_receipt(actor=self.manager, agreement_id=self.agreement.pk, receipt_id=receipt.pk, valid_goods_percentage=90)
        services.assess_goods_receipt(actor=self.bookkeeper, agreement_id=self.agreement.pk, receipt_id=receipt.pk,
                                      valid_goods_percentage=90, assessment_note="Recount confirmed another valid unit")
        event = self.agreement.events.get(event_type="GOODS_ASSESSMENT_CORRECTED")
        self.assertEqual(Decimal(event.metadata["before"]["valid_goods_percentage"]), 80)
        self.assertEqual(Decimal(event.metadata["after"]["valid_goods_percentage"]), 90)

    def test_requirement_fields_do_not_include_person_or_priority(self):
        names = {field.name for field in Requirement._meta.fields}
        self.assertFalse({"person", "responsible_person", "priority", "reminder_date"} & names)

    def test_requirement_update_and_completion(self):
        requirement = self.requirement()
        services.update_requirement(actor=self.manager, agreement_id=self.agreement.pk, requirement_id=requirement.pk,
                                     note="Await revised delivery date", deadline=date(2026, 10, 20))
        services.complete_requirement(actor=self.bookkeeper, agreement_id=self.agreement.pk, requirement_id=requirement.pk)
        requirement.refresh_from_db()
        self.assertEqual(requirement.status, "COMPLETED")
        self.assertEqual(requirement.note, "Await revised delivery date")
        self.assertIsNotNone(requirement.resolved_at)
        with self.assertRaises(ValidationError):
            services.update_requirement(actor=self.manager, agreement_id=self.agreement.pk, requirement_id=requirement.pk, note="Changed")
        with self.assertRaises(ValidationError):
            services.complete_requirement(actor=self.manager, agreement_id=self.agreement.pk, requirement_id=requirement.pk)

    def test_requirement_cancellation_is_terminal(self):
        requirement = self.requirement()
        services.cancel_requirement(actor=self.manager, agreement_id=self.agreement.pk, requirement_id=requirement.pk)
        requirement.refresh_from_db()
        self.assertEqual(requirement.status, "CANCELLED")
        with self.assertRaises(ValidationError):
            services.complete_requirement(actor=self.manager, agreement_id=self.agreement.pk, requirement_id=requirement.pk)

    def test_validation_is_a_specialized_requirement(self):
        validation = self.validation(note="Inspect delivery", deadline=date(2026, 10, 20))
        self.assertEqual(validation.requirement.note, "Inspect delivery")
        self.assertEqual(validation.requirement.deadline, date(2026, 10, 20))
        self.assertEqual(validation.requirement.exchange_agreement, self.agreement)
        self.assertEqual(validation.status, "PENDING")
        self.assertEqual(self.agreement.events.filter(event_type__in=["REQUIREMENT_CREATED", "VALIDATION_REQUESTED"]).count(), 2)

    def test_generic_requirement_resolution_cannot_bypass_validation(self):
        validation = self.validation()
        for service in (services.complete_requirement, services.cancel_requirement):
            with self.assertRaises(ValidationError):
                service(actor=self.manager, agreement_id=self.agreement.pk, requirement_id=validation.requirement_id)
        services.update_requirement(actor=self.manager, agreement_id=self.agreement.pk,
                                     requirement_id=validation.requirement_id, note="Updated context")
        validation.refresh_from_db()
        self.assertEqual(validation.status, "PENDING")

    def test_approval_and_rejection_both_complete_requirement(self):
        receipt = self.receipt()
        for decision in ("APPROVED", "REJECTED"):
            validation = self.validation()
            self.respond(validation, decision=decision)
            validation.refresh_from_db()
            self.assertEqual(validation.status, decision)
            self.assertEqual(validation.requirement.status, "COMPLETED")
            self.assertEqual(validation.recorded_by, self.manager)
        receipt.refresh_from_db()
        self.assertFalse(receipt.is_validated)

    def test_validation_cancellation_cancels_requirement(self):
        validation = self.validation()
        services.cancel_validation(actor=self.bookkeeper, agreement_id=self.agreement.pk, validation_id=validation.pk)
        validation.refresh_from_db()
        self.assertEqual(validation.status, "CANCELLED")
        self.assertEqual(validation.requirement.status, "CANCELLED")
        with self.assertRaises(ValidationError):
            self.respond(validation)

    def test_validation_response_requires_existing_pending_request(self):
        validation = self.validation()
        self.respond(validation)
        with self.assertRaises(ValidationError):
            self.respond(validation, decision="REJECTED")
        with self.assertRaises(ValidationError):
            self.respond(validation, validation_id=999999)
        self.assertEqual(self.agreement.events.filter(event_type="VALIDATION_RECEIVED").count(), 1)

    def test_invalid_response_has_no_partial_state(self):
        validation = self.validation()
        for changes in ({"decision": "CANCELLED"}, {"response_date": date(2026, 9, 30)}, {"responder": " "}):
            with self.assertRaises(ValidationError):
                self.respond(validation, **changes)
        validation.refresh_from_db()
        self.assertEqual(validation.status, "PENDING")
        self.assertEqual(validation.requirement.status, "OPEN")

    def test_actions_cannot_reference_another_case(self):
        receipt = self.receipt(agreement_id=self.other.pk)
        requirement = self.requirement(agreement_id=self.other.pk)
        validation = self.validation(agreement_id=self.other.pk)
        with self.assertRaises(ValidationError):
            services.assess_goods_receipt(actor=self.manager, agreement_id=self.agreement.pk, receipt_id=receipt.pk, valid_goods_percentage=100)
        with self.assertRaises(ValidationError):
            services.complete_requirement(actor=self.manager, agreement_id=self.agreement.pk, requirement_id=requirement.pk)
        with self.assertRaises(ValidationError):
            self.respond(validation)
        self.assertNotIn(validation, ValidationResponseForm(agreement=self.agreement).fields["validation"].queryset)

    def test_response_and_requirement_rollback_if_audit_fails(self):
        validation = self.validation()
        original = services.record_event
        def fail_on_completion(**kwargs):
            if kwargs["event_type"] == "REQUIREMENT_COMPLETED":
                raise RuntimeError("audit failed")
            return original(**kwargs)
        with patch("operations.services.record_event", side_effect=fail_on_completion):
            with self.assertRaises(RuntimeError):
                self.respond(validation)
        validation.refresh_from_db()
        self.assertEqual(validation.status, "PENDING")
        self.assertEqual(validation.requirement.status, "OPEN")
        self.assertFalse(self.agreement.events.filter(event_type="VALIDATION_RECEIVED").exists())

    def test_failed_validation_creation_does_not_leave_requirement(self):
        with self.assertRaises(ValidationError):
            self.validation(email="invalid")
        self.assertEqual(Requirement.objects.count(), 0)
        self.assertEqual(ValidationRequest.objects.count(), 0)

    def test_email_sent_only_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            validation = self.validation(send_email=True)
            self.assertEqual(len(mail.outbox), 0)
        validation.refresh_from_db()
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["sarah@example.test"])
        self.assertIn("Are these goods acceptable?", mail.outbox[0].body)
        self.assertEqual(validation.email_status, "SENT")
        with self.assertRaises(ValidationError):
            services.send_validation_request(actor=self.manager, agreement_id=self.agreement.pk, validation_id=validation.pk)

    def test_email_failure_is_recorded_and_can_be_retried(self):
        with patch("operations.messaging.send_validation_email", side_effect=RuntimeError("offline")):
            with self.captureOnCommitCallbacks(execute=True):
                validation = self.validation(send_email=True)
        validation.refresh_from_db()
        self.assertEqual(validation.status, "PENDING")
        self.assertEqual(validation.email_status, "FAILED")
        self.assertTrue(self.agreement.events.filter(event_type="VALIDATION_EMAIL_FAILED").exists())
        with self.captureOnCommitCallbacks(execute=True):
            services.send_validation_request(actor=self.manager, agreement_id=self.agreement.pk, validation_id=validation.pk)
        validation.refresh_from_db()
        self.assertEqual(validation.email_status, "SENT")

    def test_rolled_back_validation_never_sends_email(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with self.assertRaises(RuntimeError), transaction.atomic():
                self.validation(send_email=True)
                raise RuntimeError("rollback")
        self.assertEqual(callbacks, [])
        self.assertEqual(len(mail.outbox), 0)

    def test_resources_are_attached_and_audited(self):
        document = services.attach_document(actor=self.bookkeeper, agreement_id=self.agreement.pk, title="Delivery evidence",
                                            category="Receipt", date=date(2026, 10, 1), document=self.upload())
        email = services.attach_email(actor=self.manager, agreement_id=self.agreement.pk, sender="supplier@example.test",
                                      recipients="first@example.test, second@example.test", subject="Delivery", occurred_at=timezone.now(),
                                      direction="RECEIVED", body="Delivery confirmed")
        invoice = services.attach_supplier_invoice(actor=self.bookkeeper, agreement_id=self.agreement.pk, reference="INV-1",
                                                  amount=Decimal("120.50"), currency="EUR", invoice_date=date(2026, 10, 1), document=self.upload())
        for resource in (document, email, invoice):
            self.assertEqual(resource.exchange_agreement, self.agreement)
            self.assertTrue(self.agreement.events.filter(related_object_type=resource._meta.label, related_object_id=resource.pk).exists())

    def test_invalid_invoice_and_email_are_rejected(self):
        with self.assertRaises(ValidationError):
            services.attach_supplier_invoice(actor=self.manager, agreement_id=self.agreement.pk, reference="INV",
                                              amount=-1, currency="EURO", invoice_date=date(2026, 10, 1), document=self.upload())
        with self.assertRaises(ValidationError):
            services.attach_email(actor=self.manager, agreement_id=self.agreement.pk, sender="sender@example.test", recipients="invalid",
                                  subject="Message", occurred_at=timezone.now(), direction="RECEIVED", body="Body")
        self.assertEqual(SupplierInvoice.objects.count(), 0)
        self.assertEqual(CaseEmail.objects.count(), 0)

    def test_failed_attachment_removes_uploaded_file_and_record(self):
        storage = CaseDocument._meta.get_field("document").storage
        saved = []
        original_save = storage.save
        def remember(name, content, **kwargs):
            name = original_save(name, content, **kwargs)
            saved.append(name)
            return name
        with patch.object(storage, "save", side_effect=remember), patch("operations.services.record_event", side_effect=RuntimeError("audit failed")):
            with self.assertRaises(RuntimeError):
                services.attach_document(actor=self.manager, agreement_id=self.agreement.pk, title="Evidence", category="Test", date=date(2026, 10, 1), document=self.upload())
        self.assertEqual(len(saved), 1)
        self.assertFalse(storage.exists(saved[0]))
        self.assertEqual(CaseDocument.objects.count(), 0)

    def test_protected_download_checks_case_and_permissions(self):
        document = services.attach_document(actor=self.manager, agreement_id=self.agreement.pk, title="Evidence", category="Test", date=date(2026, 10, 1), document=self.upload())
        url = reverse("case-download", args=[self.agreement.pk, "document", document.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(User.objects.create_user("download-outsider"))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.bookkeeper)
        self.assertEqual(self.client.get(reverse("case-download", args=[self.other.pk, "document", document.pk])).status_code, 404)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(b"".join(response.streaming_content), b"test evidence")

    def test_closed_case_remains_readable_and_downloadable(self):
        receipt = self.receipt(document=self.upload())
        ExchangeAgreement.objects.filter(pk=self.agreement.pk).update(status="CLOSED")
        self.client.force_login(self.bookkeeper)
        response = self.client.get(reverse("agreement-detail", args=[self.agreement.pk]))
        self.assertContains(response, "This case is closed")
        self.assertNotContains(response, "Declare goods received")
        download = self.client.get(reverse("case-download", args=[self.agreement.pk, "receipt", receipt.pk]))
        self.assertEqual(b"".join(download.streaming_content), b"test evidence")
        self.assertEqual(self.client.get(reverse("case-action", args=[self.agreement.pk, "receive-goods"])).status_code, 409)

    def test_forms_create_resources_and_response(self):
        self.client.force_login(self.bookkeeper)
        for action, data in (
            ("receive-goods", {"description": "Goods", "quantity": "5", "unit": "units", "reception_date": "2026-10-01"}),
            ("create-requirement", {"note": "Wait for clarification"}),
            ("request-validation", {"person": "Sarah", "email": "sarah@example.test", "question": "Acceptable?", "request_date": "2026-10-01"}),
            ("attach-document", {"title": "Report", "category": "Inspection", "date": "2026-10-01", "document": self.upload()}),
            ("attach-invoice", {"reference": "WEB-INV", "amount": "100.00", "currency": "eur", "invoice_date": "2026-10-01", "document": self.upload()}),
            ("attach-email", {"sender": "a@example.test", "recipients": "b@example.test", "subject": "Web email", "direction": "RECEIVED", "occurred_at": "2026-10-01T09:30", "body": "Details"}),
        ):
            with self.subTest(action=action):
                response = self.client.post(reverse("case-action", args=[self.agreement.pk, action]), data)
                self.assertRedirects(response, reverse("agreement-detail", args=[self.agreement.pk]))
        validation = ValidationRequest.objects.get()
        response = self.client.post(reverse("case-action", args=[self.agreement.pk, "record-validation"]), {
            "validation": validation.pk, "decision": "REJECTED", "response_date": "2026-10-02", "responder": "Sarah", "response_comment": "Needs replacement", "document": self.upload(),
        })
        self.assertRedirects(response, reverse("agreement-detail", args=[self.agreement.pk]))
        self.assertEqual(SupplierInvoice.objects.get().currency, "EUR")

    def test_requirement_edit_and_confirmation_views(self):
        self.client.force_login(self.manager)
        requirement = self.requirement()
        url = reverse("requirement-action", args=[self.agreement.pk, requirement.pk, "complete"])
        self.assertEqual(self.client.get(url).status_code, 200)
        requirement.refresh_from_db()
        self.assertEqual(requirement.status, "OPEN")
        self.client.post(url, {})
        requirement.refresh_from_db()
        self.assertEqual(requirement.status, "OPEN")
        self.assertRedirects(self.client.post(url, {"confirm": "on"}), reverse("agreement-detail", args=[self.agreement.pk]))

    def test_post_cannot_select_validation_from_another_case(self):
        validation = self.validation(agreement_id=self.other.pk)
        self.client.force_login(self.manager)
        response = self.client.post(reverse("case-action", args=[self.agreement.pk, "record-validation"]), {
            "validation": validation.pk, "decision": "APPROVED", "response_date": "2026-10-02", "responder": "Sarah",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        validation.refresh_from_db()
        self.assertEqual(validation.status, "PENDING")

    def test_case_renders_requirements_history_and_escapes_email(self):
        self.validation()
        services.attach_email(actor=self.manager, agreement_id=self.agreement.pk, sender="a@example.test", recipients="b@example.test",
                              subject="Unsafe email", occurred_at=timezone.now(), direction="RECEIVED", body="<script>alert(1)</script>")
        self.client.force_login(self.manager)
        response = self.client.get(reverse("agreement-detail", args=[self.agreement.pk]))
        self.assertContains(response, "Outstanding requirements")
        self.assertContains(response, "Record response")
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>")

    def test_history_pagination_keeps_chronological_order(self):
        for index in range(31):
            self.requirement(note=f"Requirement {index}")
        self.client.force_login(self.manager)
        response = self.client.get(reverse("agreement-detail", args=[self.agreement.pk]))
        self.assertEqual(response.context["history_page"].number, 2)
        events = list(response.context["history_page"])
        self.assertEqual(events, sorted(events, key=lambda item: (item.timestamp, item.pk)))
        response = self.client.get(reverse("agreement-detail", args=[self.agreement.pk]), {"history_page": 1})
        self.assertEqual(response.context["history_page"].number, 1)

    def test_read_only_user_has_no_action_buttons_and_cannot_post(self):
        viewer = User.objects.create_user("case-viewer")
        viewer.user_permissions.add(Permission.objects.get(codename="access_workspace"))
        self.client.force_login(viewer)
        response = self.client.get(reverse("agreement-detail", args=[self.agreement.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Declare goods received")
        self.assertEqual(self.client.post(reverse("case-action", args=[self.agreement.pk, "create-requirement"]), {"note": "Unauthorized"}).status_code, 403)

    def test_actions_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.manager)
        response = client.post(reverse("case-action", args=[self.agreement.pk, "create-requirement"]), {"note": "Missing token"})
        self.assertEqual(response.status_code, 403)

    def test_admin_cannot_bypass_operations_services(self):
        admin = User.objects.create_superuser("operations-admin", password="test-password")
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse("admin:operations_requirement_add")).status_code, 403)
        requirement = self.requirement()
        self.assertEqual(self.client.post(reverse("admin:operations_requirement_change", args=[requirement.pk]), {"status": "COMPLETED"}).status_code, 403)


class ConcurrentValidationTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_two_responses_record_only_one_decision(self):
        call_command("setup_roles", verbosity=0)
        actor = User.objects.create_user("validation-race")
        actor.groups.add(Group.objects.get(name="Manager"))
        supplier = Supplier.objects.create(reference="RACE", name="Supplier")
        contract = create_contract(actor=actor, supplier=supplier, reference="RACE-C", title="Contract", start_date=date(2026, 1, 1))
        agreement = create_exchange_agreement(actor=actor, supplier=supplier, contract=contract)
        validation = services.request_validation(actor=actor, agreement_id=agreement.pk, person="Sarah", email="sarah@example.test",
                                                question="Acceptable?", request_date=date(2026, 10, 1))
        barrier = Barrier(2)
        def respond(decision):
            try:
                user = User.objects.get(pk=actor.pk)
                barrier.wait(timeout=10)
                try:
                    services.record_validation(actor=user, agreement_id=agreement.pk, validation_id=validation.pk,
                                               decision=decision, response_date=date(2026, 10, 2), responder="Sarah")
                    return "recorded"
                except ValidationError:
                    return "rejected"
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(respond, ["APPROVED", "REJECTED"]))
        self.assertCountEqual(results, ["recorded", "rejected"])
        self.assertEqual(agreement.events.filter(event_type="VALIDATION_RECEIVED").count(), 1)
        self.assertEqual(agreement.events.filter(event_type="REQUIREMENT_COMPLETED").count(), 1)
