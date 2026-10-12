from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, connections, transaction
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse

from accounts.models import User
from agreements.forms import ExchangeAgreementForm
from agreements.models import ExchangeAgreement
from agreements.services import create_exchange_agreement
from audit.models import BusinessEvent
from suppliers.models import Supplier
from .forms import PurchaseOrderForm
from .models import Contract, InternalPolicy, PurchaseOrder
from .services import (create_contract, create_internal_policy, create_purchase_order,
                       update_contract, delete_contract, update_internal_policy,
                       delete_internal_policy, update_purchase_order, delete_purchase_order)


class MaintenanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_roles", verbosity=0)
        cls.manager = User.objects.create_user("manager-maintenance")
        cls.bookkeeper = User.objects.create_user("bookkeeper-maintenance")
        cls.manager.groups.add(Group.objects.get(name="Manager"))
        cls.bookkeeper.groups.add(Group.objects.get(name="Bookkeeper"))
        cls.admin = User.objects.create_superuser("admin-maintenance", password="test-password")
        cls.supplier = Supplier.objects.create(reference="SUP", name="Supplier")
        cls.contract = create_contract(actor=cls.manager, reference="C", supplier=cls.supplier,
                                       title="Contract", start_date=date(2026, 1, 1))
        cls.policy = InternalPolicy.objects.create(reference="POL", version="1", title="Policy", document="policy.pdf")

    def po(self, **changes):
        data = dict(actor=self.bookkeeper, reference="PO", supplier=self.supplier,
                    description="Order", order_date=date(2026, 1, 1))
        data.update(changes)
        return create_purchase_order(**data)

    def agreement(self, **changes):
        data = dict(actor=self.bookkeeper, supplier=self.supplier, contract=self.contract)
        data.update(changes)
        return create_exchange_agreement(**data)

    def test_po_default_is_independent_and_form_defaults_no(self):
        po, agreement = self.po()
        self.assertIsNone(agreement)
        self.assertFalse(po.exchange_agreements.exists())
        self.assertEqual(PurchaseOrderForm()["create_agreement"].value(), "no")
        self.client.force_login(self.bookkeeper)
        response = self.client.post(reverse("po-create"), {
            "reference": "WEB", "supplier": self.supplier.pk, "description": "Order",
            "order_date": "2026-01-01", "create_agreement": "no",
        })
        self.assertRedirects(response, reverse("po-detail", args=[PurchaseOrder.objects.get(reference="WEB").pk]))
        self.assertEqual(ExchangeAgreement.objects.count(), 0)

    def test_opt_in_copies_contract_and_policy(self):
        po, agreement = self.po(contract=self.contract, create_agreement=True, internal_policy=self.policy)
        self.assertEqual(agreement.contract, self.contract)
        self.assertEqual(agreement.internal_policy, self.policy)
        self.assertEqual(agreement.purchase_order, po)

    def test_policy_without_agreement_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.po(internal_policy=self.policy)
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_no_second_agreement_in_service_form_or_database(self):
        po, agreement = self.po(create_agreement=True)
        ExchangeAgreement.objects.filter(pk=agreement.pk).update(status="CLOSED")
        with self.assertRaises(ValidationError):
            self.agreement(purchase_order=po)
        self.assertNotIn(po, ExchangeAgreementForm().fields["purchase_order"].queryset)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExchangeAgreement.objects.create(supplier=self.supplier, purchase_order=po,
                                             responsible_user=self.manager, created_by=self.manager)
        self.assertEqual(ExchangeAgreement.objects.count(), 1)

    def test_unused_po_can_be_edited_then_used(self):
        po, _ = self.po()
        update_purchase_order(actor=self.bookkeeper, pk=po.pk, description="Revised", contract=self.contract)
        po.refresh_from_db()
        self.assertEqual(po.description, "Revised")
        self.agreement(purchase_order=po)
        with self.assertRaises(ValidationError):
            update_purchase_order(actor=self.manager, pk=po.pk, description="Forbidden")
        with self.assertRaises(ValidationError):
            delete_purchase_order(actor=self.bookkeeper, pk=po.pk)

    def test_unused_po_can_be_deleted_with_history(self):
        po, _ = self.po()
        pk = po.pk
        delete_purchase_order(actor=self.bookkeeper, pk=pk)
        self.assertFalse(PurchaseOrder.objects.filter(pk=pk).exists())
        event = BusinessEvent.objects.get(event_type="PURCHASE_ORDER_DELETED")
        self.assertEqual(event.related_object_id, pk)
        self.assertEqual(event.metadata["before"]["reference"], "PO")

    def test_manager_and_superuser_can_edit_unused_contract_and_policy(self):
        for actor in (self.manager, self.admin):
            update_contract(actor=actor, pk=self.contract.pk, title=actor.username)
            update_internal_policy(actor=actor, pk=self.policy.pk, title=actor.username)
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.title, self.admin.username)
        event = BusinessEvent.objects.filter(event_type="CONTRACT_UPDATED").last()
        self.assertEqual(event.metadata["before"]["title"], self.manager.username)
        self.assertEqual(event.metadata["after"]["title"], self.admin.username)

    def test_bookkeeper_cannot_maintain_contract_or_policy(self):
        for action, record in ((update_contract, self.contract), (delete_contract, self.contract),
                               (update_internal_policy, self.policy), (delete_internal_policy, self.policy)):
            with self.assertRaises(PermissionDenied):
                action(actor=self.bookkeeper, pk=record.pk)
        self.client.force_login(self.bookkeeper)
        for kind, obj in (("contract", self.contract), ("policy", self.policy)):
            self.assertEqual(self.client.post(reverse("record-delete", args=[kind, obj.pk])).status_code, 403)
            self.assertEqual(self.client.get(reverse("record-edit", args=[kind, obj.pk])).status_code, 403)

    def test_both_open_and_closed_agreements_lock_contract_policy_po(self):
        po, _ = self.po()
        agreement = self.agreement(purchase_order=po, internal_policy=self.policy)
        for status in ("OPEN", "CLOSED"):
            ExchangeAgreement.objects.filter(pk=agreement.pk).update(status=status)
            for action, obj, changes in (
                (update_contract, self.contract, {"title": "Changed"}), (delete_contract, self.contract, {}),
                (update_internal_policy, self.policy, {"title": "Changed"}), (delete_internal_policy, self.policy, {}),
                (update_purchase_order, po, {"description": "Changed"}), (delete_purchase_order, po, {}),
            ):
                with self.subTest(status=status, action=action.__name__):
                    with self.assertRaises(ValidationError):
                        action(actor=self.admin, pk=obj.pk, **changes)

    def test_contract_indirectly_used_via_po_is_locked(self):
        po, _ = self.po(contract=self.contract)
        self.agreement(contract=None, purchase_order=po)
        self.assertTrue(self.contract.is_used)
        with self.assertRaises(ValidationError):
            update_contract(actor=self.manager, pk=self.contract.pk, title="Changed")
        with self.assertRaises(ValidationError):
            delete_contract(actor=self.manager, pk=self.contract.pk)

    def test_contract_with_unused_po_editable_but_not_deletable(self):
        po, _ = self.po(contract=self.contract)
        update_contract(actor=self.manager, pk=self.contract.pk, title="Revised")
        other = Supplier.objects.create(reference="OTHER", name="Other")
        with self.assertRaises(ValidationError):
            update_contract(actor=self.manager, pk=self.contract.pk, supplier=other)
        with self.assertRaises(ValidationError):
            delete_contract(actor=self.manager, pk=self.contract.pk)
        update_purchase_order(actor=self.bookkeeper, pk=po.pk, contract=None)
        delete_contract(actor=self.manager, pk=self.contract.pk)
        self.assertFalse(Contract.objects.exists())

    def test_unused_policy_deletion_retains_audit(self):
        pk = self.policy.pk
        delete_internal_policy(actor=self.admin, pk=pk)
        event = BusinessEvent.objects.get(event_type="POLICY_DELETED")
        self.assertEqual(event.metadata["before"]["version"], "1")
        self.assertFalse(InternalPolicy.objects.filter(pk=pk).exists())

    def test_edit_and_delete_roll_back_when_audit_fails(self):
        with patch("contracts.services.record_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                update_contract(actor=self.manager, pk=self.contract.pk, title="Changed")
            with self.assertRaises(RuntimeError):
                delete_contract(actor=self.manager, pk=self.contract.pk)
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.title, "Contract")

    def test_edit_form_updates_and_delete_requires_post(self):
        self.client.force_login(self.manager)
        response = self.client.post(reverse("record-edit", args=["contract", self.contract.pk]), {
            "reference": "C", "supplier": self.supplier.pk, "title": "Revised", "start_date": "2026-01-01",
        })
        self.assertRedirects(response, reverse("contract-list"))
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.title, "Revised")
        delete_url = reverse("record-delete", args=["contract", self.contract.pk])
        self.assertContains(self.client.get(delete_url), "Confirm deletion")
        self.assertTrue(Contract.objects.filter(pk=self.contract.pk).exists())
        self.assertRedirects(self.client.post(delete_url), reverse("contract-list"))

    def test_used_record_views_reject_stale_forms(self):
        self.client.force_login(self.manager)
        self.agreement()
        for route in ("record-edit", "record-delete"):
            self.assertEqual(self.client.post(reverse(route, args=["contract", self.contract.pk]), {}).status_code, 409)

    def test_admin_routes_reuse_maintenance_forms(self):
        self.client.force_login(self.admin)
        self.assertRedirects(self.client.get(reverse("admin:contracts_contract_change", args=[self.contract.pk])),
                             reverse("record-edit", args=["contract", self.contract.pk]))
        self.assertRedirects(self.client.get(reverse("admin:contracts_internalpolicy_delete", args=[self.policy.pk])),
                             reverse("record-delete", args=["policy", self.policy.pk]))
        self.agreement()
        self.assertEqual(self.client.get(reverse("admin:contracts_contract_change", args=[self.contract.pk])).status_code, 200)

    def test_used_document_cannot_be_replaced(self):
        self.agreement(internal_policy=self.policy)
        with self.assertRaises(ValidationError):
            update_internal_policy(actor=self.admin, pk=self.policy.pk, document=SimpleUploadedFile("new.pdf", b"replacement"))
        self.policy.refresh_from_db()
        self.assertEqual(self.policy.document.name, "policy.pdf")

    def test_policy_creation_uses_service_and_audit(self):
        self.client.force_login(self.manager)
        response = self.client.post(reverse("policy-create"), {
            "reference": "NEW", "version": "1", "title": "New policy",
            "document": SimpleUploadedFile("new.pdf", b"policy"),
        })
        self.assertRedirects(response, reverse("policy-list"))
        policy = InternalPolicy.objects.get(reference="NEW")
        self.addCleanup(policy.document.delete, save=False)
        self.assertTrue(BusinessEvent.objects.filter(event_type="POLICY_CREATED", related_object_id=policy.pk).exists())


class ConcurrentPOTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_simultaneous_conversion_creates_one_agreement(self):
        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("concurrent-user")
        user.groups.add(Group.objects.get(name="Bookkeeper"))
        supplier = Supplier.objects.create(reference="CONCURRENT", name="Supplier")
        po, _ = create_purchase_order(actor=user, supplier=supplier, reference="PO-CONCURRENT",
                                      description="Order", order_date=date(2026, 1, 1))
        barrier = Barrier(2)

        def convert():
            try:
                actor = User.objects.get(pk=user.pk)
                order = PurchaseOrder.objects.get(pk=po.pk)
                vendor = Supplier.objects.get(pk=supplier.pk)
                barrier.wait(timeout=10)
                try:
                    create_exchange_agreement(actor=actor, supplier=vendor, purchase_order=order)
                    return "created"
                except ValidationError:
                    return "rejected"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: convert(), range(2)))
        self.assertCountEqual(results, ["created", "rejected"])
        self.assertEqual(ExchangeAgreement.objects.filter(purchase_order=po).count(), 1)
        self.assertEqual(BusinessEvent.objects.filter(event_type="EXCHANGE_AGREEMENT_CREATED").count(), 1)
