from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connections, transaction
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone
from accounts.models import User
from agreements.models import ExchangeAgreement
from agreements.services import create_exchange_agreement, close_exchange_agreement, reopen_exchange_agreement
from audit.models import BusinessEvent
from contracts.services import create_contract
from operations.services import create_requirement, request_validation, declare_goods_received
from suppliers.models import Supplier
from . import services
from .models import DigitalInvoice, Payment


def fixture(owner):
    call_command("setup_roles", verbosity=0)
    owner.manager = User.objects.create_user("billing-manager", password="test-password")
    owner.bookkeeper = User.objects.create_user("billing-bookkeeper", password="test-password")
    owner.manager.groups.add(Group.objects.get(name="Manager"))
    owner.bookkeeper.groups.add(Group.objects.get(name="Bookkeeper"))
    owner.supplier = Supplier.objects.create(reference="BILL", name="Billing supplier")
    contract = create_contract(actor=owner.manager, supplier=owner.supplier, reference="BILL-C", title="Materials", start_date=timezone.localdate())
    owner.agreement = create_exchange_agreement(actor=owner.bookkeeper, supplier=owner.supplier, contract=contract)
    owner.other = create_exchange_agreement(actor=owner.manager, supplier=owner.supplier, contract=contract)


class InvoiceHelpers:
    def invoice(self, **changes):
        data = dict(actor=self.bookkeeper, agreement_id=self.agreement.pk, description="First delivery", calculation="10 units at 12 EUR", amount=Decimal("120.00"), currency="EUR", due_date=timezone.localdate())
        data.update(changes)
        return services.create_digital_invoice(**data)

    def action(self, invoice, action, **changes):
        data = dict(actor=self.bookkeeper, agreement_id=self.agreement.pk, invoice_id=invoice.pk, expected_revision=invoice.revision)
        data.update(changes)
        if action == "pay":
            return services.execute_payment(**data)
        return services.transition_invoice(action=action, **data)

    def edit(self, invoice, **changes):
        data = {field: getattr(invoice, field) for field in services.EDITABLE_FIELDS}
        data.update(changes)
        return services.update_digital_invoice(actor=self.bookkeeper, agreement_id=self.agreement.pk, invoice_id=invoice.pk, expected_revision=invoice.revision, **data)


class BillingTests(InvoiceHelpers, TestCase):
    @classmethod
    def setUpTestData(cls):
        fixture(cls)

    def test_both_roles_can_create_ready_and_pay(self):
        for actor in (self.manager, self.bookkeeper):
            invoice = self.invoice(actor=actor)
            invoice = self.action(invoice, "ready", actor=actor)
            payment = self.action(invoice, "pay", actor=actor)
            invoice.refresh_from_db()
            self.assertEqual(invoice.status, "PAID")
            self.assertEqual(payment.amount, Decimal("120.00"))
            self.assertEqual(payment.executed_at, invoice.paid_at)
            self.assertTrue(payment.reference.startswith("SIM-"))

    def test_unrelated_open_requirements_do_not_block_partial_payment(self):
        create_requirement(actor=self.bookkeeper, agreement_id=self.agreement.pk, note="Await second delivery")
        self.action(self.action(self.invoice(), "ready"), "pay")
        self.assertEqual(self.agreement.requirements.get().status, "OPEN")

    def test_every_edit_resets_readiness_and_audits_before_after(self):
        for field, value in (("amount", Decimal("90.00")), ("currency", "USD"), ("description", "Half delivery"), ("calculation", "Adjusted"), ("due_date", timezone.localdate() + timedelta(days=3))):
            invoice = self.action(self.invoice(), "ready")
            updated = self.edit(invoice, **{field: value})
            self.assertEqual(updated.status, "DRAFT")
            event = BusinessEvent.objects.filter(event_type="DIGITAL_INVOICE_UPDATED").last()
            self.assertEqual(event.metadata["before"]["status"], "READY_TO_PAY")
            self.assertEqual(event.metadata["after"]["status"], "DRAFT")

    def test_noop_edit_does_not_reset_readiness(self):
        invoice = self.action(self.invoice(), "ready")
        self.assertEqual(self.edit(invoice).status, "READY_TO_PAY")

    def test_invalid_amount_and_currency_are_rejected(self):
        for changes in ({"amount": Decimal("0")}, {"amount": Decimal("-1")}, {"currency": "eur"}, {"currency": "EU"}):
            with self.assertRaises(ValidationError):
                self.invoice(**changes)
        self.assertFalse(DigitalInvoice.objects.exists())

    def test_paid_and_cancelled_records_are_immutable(self):
        paid = self.action(self.invoice(), "ready")
        self.action(paid, "pay")
        paid.refresh_from_db()
        cancelled = self.action(self.invoice(), "cancel")
        for invoice in (paid, cancelled):
            with self.assertRaises(ValidationError):
                self.edit(invoice, amount=Decimal("10.00"))
            for action in ("ready", "draft", "cancel", "pay"):
                with self.assertRaises(ValidationError):
                    self.action(invoice, action)

    def test_ready_can_be_drafted_or_cancelled(self):
        invoice = self.action(self.invoice(), "ready")
        self.assertEqual(self.action(invoice, "draft").status, "DRAFT")
        invoice = self.action(self.invoice(), "ready")
        self.assertEqual(self.action(invoice, "cancel").status, "CANCELLED")

    def test_draft_payment_rejected(self):
        with self.assertRaises(ValidationError):
            self.action(self.invoice(), "pay")
        self.assertFalse(Payment.objects.exists())

    def test_stale_payment_confirmation_rejected_after_edit_and_reready(self):
        original = self.action(self.invoice(), "ready")
        updated = self.edit(original, amount=Decimal("250.00"))
        self.action(updated, "ready")
        with self.assertRaisesMessage(ValidationError, "changed since"):
            self.action(original, "pay")
        self.assertFalse(Payment.objects.exists())

    def test_cross_agreement_invoice_cannot_be_changed(self):
        with self.assertRaises(ValidationError):
            self.action(self.invoice(), "ready", agreement_id=self.other.pk)

    def test_unprivileged_actor_cannot_write_or_close(self):
        user = User.objects.create_user("outsider")
        invoice = self.invoice()
        for operation in (lambda: self.invoice(actor=user), lambda: self.action(invoice, "ready", actor=user), lambda: self.action(invoice, "pay", actor=user), lambda: self.action(invoice, "cancel", actor=user), lambda: close_exchange_agreement(actor=user, agreement_id=self.agreement.pk, reason="COMPLETED")):
            with self.assertRaises(PermissionDenied):
                operation()

    def test_payment_and_invoice_update_roll_back_on_audit_failure(self):
        invoice = self.action(self.invoice(), "ready")
        count = BusinessEvent.objects.count()
        with patch("billing.services.record_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.action(invoice, "pay")
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "READY_TO_PAY")
        self.assertFalse(Payment.objects.exists())
        self.assertEqual(BusinessEvent.objects.count(), count)

    def test_database_rejects_duplicate_payments_and_negative_amounts(self):
        payment = self.action(self.action(self.invoice(), "ready"), "pay")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Payment.objects.create(invoice=payment.invoice, amount=payment.amount, currency="EUR", executed_by=self.manager)
        with self.assertRaises(IntegrityError), transaction.atomic():
            DigitalInvoice.objects.filter(pk=payment.invoice_id).update(amount=-1)

    def test_overdue_excludes_paid_cancelled_and_today(self):
        overdue = self.invoice(due_date=timezone.localdate() - timedelta(days=1))
        self.assertTrue(overdue.is_overdue)
        self.assertFalse(self.invoice().is_overdue)
        self.assertFalse(self.action(overdue, "cancel").is_overdue)

    def test_close_cancels_outstanding_work_but_keeps_drafts_and_receipts(self):
        requirement = create_requirement(actor=self.bookkeeper, agreement_id=self.agreement.pk, note="Missing information")
        validation = request_validation(actor=self.bookkeeper, agreement_id=self.agreement.pk, person="Reviewer", email="reviewer@example.test", question="Accept delivery?", request_date=timezone.localdate())
        receipt = declare_goods_received(actor=self.bookkeeper, agreement_id=self.agreement.pk, description="Materials", quantity=1, reception_date=timezone.localdate())
        invoice = self.invoice()
        closed = close_exchange_agreement(actor=self.bookkeeper, agreement_id=self.agreement.pk, reason="CANCELLED", note="Delivery incomplete")
        requirement.refresh_from_db(); validation.refresh_from_db(); invoice.refresh_from_db(); receipt.refresh_from_db()
        self.assertEqual(closed.status, "CLOSED")
        self.assertEqual(requirement.status, "CANCELLED")
        self.assertEqual(validation.status, "CANCELLED")
        self.assertEqual(invoice.status, "DRAFT")
        self.assertFalse(receipt.is_validated)
        self.assertEqual(BusinessEvent.objects.filter(event_type="REQUIREMENT_CANCELLED").count(), 2)
        reopened = reopen_exchange_agreement(actor=self.manager, agreement_id=closed.pk)
        requirement.refresh_from_db()
        self.assertEqual(reopened.status, "OPEN")
        self.assertEqual(requirement.status, "CANCELLED")
        self.assertEqual(BusinessEvent.objects.filter(event_type="EXCHANGE_AGREEMENT_REOPENED").get().metadata["previous_closure"]["reason"], "CANCELLED")

    def test_ready_invoice_blocks_close_without_cancelling_requirements(self):
        requirement = create_requirement(actor=self.bookkeeper, agreement_id=self.agreement.pk, note="Pending")
        self.action(self.invoice(), "ready")
        with self.assertRaises(ValidationError):
            close_exchange_agreement(actor=self.manager, agreement_id=self.agreement.pk, reason="COMPLETED")
        requirement.refresh_from_db()
        self.assertEqual(requirement.status, "OPEN")

    def test_closure_rolls_back_all_cancellations_on_audit_failure(self):
        requirement = create_requirement(actor=self.manager, agreement_id=self.agreement.pk, note="Pending")
        with patch("agreements.services.record_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                close_exchange_agreement(actor=self.manager, agreement_id=self.agreement.pk, reason="COMPLETED")
        requirement.refresh_from_db(); self.agreement.refresh_from_db()
        self.assertEqual(requirement.status, "OPEN")
        self.assertEqual(self.agreement.status, "OPEN")
        self.assertFalse(BusinessEvent.objects.filter(event_type="REQUIREMENT_CANCELLED").exists())

    def test_closed_case_rejects_billing_writes(self):
        invoice = self.invoice()
        close_exchange_agreement(actor=self.manager, agreement_id=self.agreement.pk, reason="COMPLETED")
        for action in (lambda: self.invoice(), lambda: self.edit(invoice), lambda: self.action(invoice, "ready"), lambda: self.action(invoice, "cancel"), lambda: self.action(invoice, "pay")):
            with self.assertRaisesMessage(ValidationError, "closed"):
                action()

    def test_invoice_tab_list_filters_and_drawer_submission(self):
        self.client.force_login(self.bookkeeper)
        overdue = self.invoice(due_date=timezone.localdate() - timedelta(days=1))
        self.invoice(agreement_id=self.other.pk)
        url = reverse("agreement-detail", args=[self.agreement.pk])
        response = self.client.get(url, {"tab": "invoices"})
        self.assertEqual(response.context["active_tab"], "invoices")
        self.assertContains(response, overdue.reference)
        response = self.client.get(reverse("digital-invoice-list"), {"overdue": "1", "supplier": self.supplier.pk, "agreement": self.agreement.pk, "status": "DRAFT"})
        self.assertEqual(response.context["page_obj"].paginator.count, 1)
        url = reverse("digital-invoice-action", args=[self.agreement.pk, overdue.pk, "ready"]) + "?panel=1"
        response = self.client.get(url)
        self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")
        self.assertNotContains(response, 'class="main-nav"')
        response = self.client.post(url, {"expected_revision": overdue.revision, "confirm": "on"})
        self.assertContains(response, "data-action-saved")

    def test_http_csrf_and_permission_guards(self):
        from django.test import Client
        invoice = self.invoice()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.bookkeeper)
        url = reverse("digital-invoice-action", args=[self.agreement.pk, invoice.pk, "ready"])
        self.assertEqual(client.post(url, {"confirm": "on", "expected_revision": 1}).status_code, 403)
        outsider = User.objects.create_user("viewer")
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get(reverse("agreement-close", args=[self.agreement.pk])).status_code, 403)

    def test_lifecycle_forms_preview_and_reopen(self):
        self.client.force_login(self.bookkeeper)
        invoice = self.action(self.invoice(), "ready")
        url = reverse("agreement-close", args=[self.agreement.pk]) + "?panel=1"
        self.assertContains(self.client.get(url), "Ready-to-pay invoices block closure")
        self.action(invoice, "draft")
        response = self.client.post(url, {"reason": "COMPLETED", "confirm": "on"})
        self.assertContains(response, "data-action-saved")
        response = self.client.post(reverse("agreement-reopen", args=[self.agreement.pk]) + "?panel=1", {"confirm": "on"})
        self.assertContains(response, "data-action-saved")


@skipUnlessDBFeature("has_select_for_update")
class BillingConcurrencyTests(InvoiceHelpers, TransactionTestCase):
    def setUp(self):
        fixture(self)

    def race(self, first, second):
        barrier = Barrier(2)
        def run(operation):
            connections.close_all()
            try:
                actor = User.objects.get(pk=self.bookkeeper.pk)
                barrier.wait(timeout=10)
                operation(actor)
                return "ok"
            except ValidationError:
                return "rejected"
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, operation) for operation in (first, second)]
            return [future.result(timeout=20) for future in futures]

    def test_concurrent_payment_attempts_record_one_payment(self):
        invoice = self.action(self.invoice(), "ready")
        pay = lambda actor: self.action(invoice, "pay", actor=actor)
        self.assertCountEqual(self.race(pay, pay), ["ok", "rejected"])
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(BusinessEvent.objects.filter(event_type="PAYMENT_EXECUTED").count(), 1)

    def test_closing_and_readiness_cannot_leave_ready_invoice_in_closed_case(self):
        invoice = self.invoice()
        close = lambda actor: close_exchange_agreement(actor=actor, agreement_id=self.agreement.pk, reason="COMPLETED")
        ready = lambda actor: self.action(invoice, "ready", actor=actor)
        self.assertCountEqual(self.race(close, ready), ["ok", "rejected"])
        invoice.refresh_from_db(); self.agreement.refresh_from_db()
        self.assertFalse(invoice.status == "READY_TO_PAY" and self.agreement.status == "CLOSED")
