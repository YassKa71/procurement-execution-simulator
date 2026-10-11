from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from agreements.models import ExchangeAgreement
from agreements.services import create_exchange_agreement
from audit.models import BusinessEvent
from suppliers.models import Supplier
from .models import Contract, InternalPolicy, PurchaseOrder
from .services import create_contract, create_purchase_order


class CommercialRecordsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_roles", verbosity=0)
        cls.manager = User.objects.create_user("commercial-manager", password="test-password")
        cls.bookkeeper = User.objects.create_user("commercial-bookkeeper", password="test-password")
        cls.manager.groups.add(Group.objects.get(name="Manager"))
        cls.bookkeeper.groups.add(Group.objects.get(name="Bookkeeper"))
        cls.supplier = Supplier.objects.create(reference="SUP-1", name="Acme Materials")
        cls.other_supplier = Supplier.objects.create(reference="SUP-2", name="Other Supplier")
        cls.contract = create_contract(actor=cls.manager, reference="C-1", supplier=cls.supplier,
                                       title="Materials framework", start_date=date(2026, 1, 1))

    def po(self, **overrides):
        values = dict(actor=self.bookkeeper, reference="PO-1", supplier=self.supplier,
                      description="100 units at EUR 20 each", create_agreement=True, order_date=date(2026, 9, 1))
        values.update(overrides)
        return create_purchase_order(**values)

    def agreement(self, **overrides):
        values = dict(actor=self.manager, supplier=self.supplier, contract=self.contract)
        values.update(overrides)
        return create_exchange_agreement(**values)

    def test_po_creation_by_both_roles_with_and_without_contract(self):
        for index, actor in enumerate((self.manager, self.bookkeeper)):
            for linked in (False, True):
                with self.subTest(actor=actor.username, linked=linked):
                    po, agreement = self.po(actor=actor, reference=f"PO-{index}-{linked}",
                                            contract=self.contract if linked else None)
                    self.assertEqual(agreement.purchase_order, po)
                    self.assertEqual(agreement.contract, self.contract if linked else None)
                    self.assertEqual(agreement.responsible_user, actor)
                    self.assertEqual(agreement.status, "OPEN")
                    self.assertEqual(po.exchange_agreements.count(), 1)
                    self.assertEqual(agreement.events.count(), 2)
                    self.assertEqual(set(agreement.events.values_list("actor_user_id", flat=True)), {actor.pk})

    def test_bookkeeper_can_create_agreement_but_not_contract(self):
        with self.assertRaises(PermissionDenied):
            create_contract(actor=self.bookkeeper, reference="DENIED", supplier=self.supplier,
                            title="No", start_date=date(2026, 1, 1))
        self.agreement(actor=self.bookkeeper)
        self.assertFalse(Contract.objects.filter(reference="DENIED").exists())
        self.assertEqual(ExchangeAgreement.objects.count(), 1)

    def test_unassigned_user_cannot_create_po(self):
        with self.assertRaises(PermissionDenied):
            self.po(actor=User.objects.create_user("unassigned-commercial"))
        self.assertEqual(PurchaseOrder.objects.count(), 0)

    def test_contract_only_po_only_and_both_are_valid(self):
        self.agreement()
        po, _ = self.po(create_agreement=False)
        self.agreement(contract=None, purchase_order=po)
        po2, _ = self.po(reference="PO-2", create_agreement=False)
        self.agreement(contract=self.contract, purchase_order=po2)
        self.assertEqual(ExchangeAgreement.objects.count(), 3)

    def test_missing_basis_rejected_by_service_and_database(self):
        with self.assertRaises(ValidationError):
            self.agreement(contract=None)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExchangeAgreement.objects.create(supplier=self.supplier, created_by=self.manager,
                                             responsible_user=self.manager)
        self.assertEqual(ExchangeAgreement.objects.count(), 0)

    def test_supplier_mismatches_rejected(self):
        with self.assertRaises(ValidationError):
            self.po(supplier=self.other_supplier, contract=self.contract)
        with self.assertRaises(ValidationError):
            self.agreement(supplier=self.other_supplier)
        po, _ = self.po()
        with self.assertRaises(ValidationError):
            self.agreement(supplier=self.other_supplier, contract=None, purchase_order=po)

    def test_conflicting_contracts_rejected(self):
        other = create_contract(actor=self.manager, supplier=self.supplier, reference="C-2",
                                title="Other framework", start_date=date(2026, 1, 1))
        po, _ = self.po(contract=self.contract)
        with self.assertRaises(ValidationError):
            self.agreement(contract=other, purchase_order=po)

    def test_po_and_agreement_roll_back_if_last_event_fails(self):
        count = BusinessEvent.objects.count()
        with patch("contracts.services.record_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.po()
        self.assertEqual(PurchaseOrder.objects.count(), 0)
        self.assertEqual(ExchangeAgreement.objects.count(), 0)
        self.assertEqual(BusinessEvent.objects.count(), count)

    def test_direct_agreement_rolls_back_on_event_failure(self):
        with patch("agreements.services.record_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.agreement()
        self.assertEqual(ExchangeAgreement.objects.count(), 0)

    def test_duplicate_po_does_not_create_another_agreement(self):
        self.po()
        with self.assertRaises(ValidationError):
            self.po()
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        self.assertEqual(ExchangeAgreement.objects.count(), 1)

    def test_invalid_contract_dates(self):
        with self.assertRaises(ValidationError):
            create_contract(actor=self.manager, reference="BAD", supplier=self.supplier,
                            title="Invalid", start_date=date(2026, 2, 1), end_date=date(2026, 1, 1))

    def test_contract_lifecycle_is_derived_separately_from_renewable(self):
        today = timezone.localdate()
        contract = Contract(start_date=today, renewable=True)
        self.assertEqual(contract.lifecycle, "Active")
        contract.end_date = today - timedelta(days=1)
        contract.start_date = today - timedelta(days=2)
        self.assertEqual(contract.lifecycle, "Expired")
        self.assertTrue(contract.renewable)
        contract.start_date = today + timedelta(days=1)
        contract.end_date = None
        self.assertEqual(contract.lifecycle, "Upcoming")

    def test_policy_version_is_pinned_and_unique(self):
        policy = InternalPolicy.objects.create(reference="POL-1", version="1", title="Purchasing", document="policies/v1.pdf")
        agreement = self.agreement(internal_policy=policy)
        InternalPolicy.objects.create(reference="POL-1", version="2", title="Purchasing", document="policies/v2.pdf")
        agreement.refresh_from_db()
        self.assertEqual(agreement.internal_policy, policy)
        with self.assertRaises(IntegrityError), transaction.atomic():
            InternalPolicy.objects.create(reference="POL-1", version="1", title="Duplicate", document="duplicate.pdf")
        with self.assertRaises(ProtectedError):
            policy.delete()

    def test_referenced_contract_cannot_be_deleted(self):
        self.agreement()
        with self.assertRaises(ProtectedError):
            self.contract.delete()

    def test_contract_event_contains_actor_and_reference(self):
        event = BusinessEvent.objects.get(event_type="CONTRACT_CREATED")
        self.assertEqual(event.related_object_id, self.contract.pk)
        self.assertEqual(event.actor_user, self.manager)
        self.assertEqual(event.related_object_type, "contracts.Contract")

    def test_bookkeeper_po_form_redirects_to_created_case(self):
        self.client.force_login(self.bookkeeper)
        response = self.client.post(reverse("po-create"), {
            "reference": "PO-WEB", "supplier": self.supplier.pk, "contract": self.contract.pk,
            "order_date": "2026-09-01", "description": "50 boxes at EUR 30 each", "create_agreement": "yes",
        })
        agreement = ExchangeAgreement.objects.get()
        self.assertRedirects(response, reverse("agreement-detail", args=[agreement.pk]))
        self.assertEqual(agreement.created_by, self.bookkeeper)
        self.assertEqual(self.client.get(reverse("contract-create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("agreement-create")).status_code, 200)

    def test_manager_forms_create_contract_and_direct_agreement(self):
        self.client.force_login(self.manager)
        response = self.client.post(reverse("contract-create"), {
            "reference": "C-WEB", "supplier": self.supplier.pk, "title": "Web contract", "start_date": "2026-09-01",
        })
        self.assertRedirects(response, reverse("contract-list"))
        response = self.client.post(reverse("agreement-create"), {
            "supplier": self.supplier.pk, "contract": self.contract.pk, "start_date": "2026-09-01",
        })
        self.assertRedirects(response, reverse("agreement-detail", args=[ExchangeAgreement.objects.get().pk]))

    def test_invalid_form_displays_error_without_writes(self):
        self.client.force_login(self.manager)
        response = self.client.post(reverse("agreement-create"), {"supplier": self.supplier.pk, "start_date": "2026-09-01"})
        self.assertContains(response, "Select a contract, a purchase order, or both.")
        self.assertEqual(ExchangeAgreement.objects.count(), 0)
        response = self.client.post(reverse("po-create"), {"reference": "BAD", "supplier": self.other_supplier.pk,
                                    "contract": self.contract.pk, "order_date": "2026-09-01", "description": "Mismatch"})
        self.assertContains(response, "The contract must belong to the selected supplier.")
        self.assertEqual(PurchaseOrder.objects.count(), 0)

    def test_pages_render_and_contract_filter_works(self):
        _, linked = self.po(contract=self.contract)
        _, unlinked = self.po(reference="PO-UNLINKED")
        self.client.force_login(self.bookkeeper)
        for name in ("contract-list", "po-list", "agreement-list", "po-create"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        self.assertEqual(self.client.get(reverse("po-detail", args=[linked.purchase_order_id])).status_code, 200)
        response = self.client.get(reverse("agreement-list"), {"contract": self.contract.pk})
        self.assertContains(response, linked.reference)
        self.assertNotContains(response, unlinked.reference)
        self.assertEqual(self.client.get(reverse("agreement-list"), {"supplier": "bad-id"}).status_code, 200)

    def test_documents_require_authenticated_workspace_permission(self):
        contract = create_contract(actor=self.manager, reference="C-FILE", supplier=self.supplier,
                                   title="With document", start_date=date(2026, 1, 1),
                                   document=SimpleUploadedFile("contract.pdf", b"example document"))
        self.addCleanup(contract.document.delete, save=False)
        url = reverse("resource-download", args=["contract", contract.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(User.objects.create_user("no-document-access"))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.bookkeeper)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"example document")
        self.assertIn("attachment", response["Content-Disposition"])
        # Consuming streaming_content closes the test response automatically.
        self.assertEqual(self.client.get(reverse("resource-download", args=["contract", self.contract.pk])).status_code, 404)

    def test_admin_cannot_bypass_po_creation_or_edit_audit(self):
        admin = User.objects.create_superuser("commercial-admin", password="test-password")
        self.client.force_login(admin)
        self.assertRedirects(self.client.get(reverse("admin:contracts_purchaseorder_add")), reverse("po-create"))
        event = BusinessEvent.objects.first()
        self.assertEqual(self.client.post(reverse("admin:audit_businessevent_change", args=[event.pk]), {"summary": "changed"}).status_code, 403)
