from uuid import uuid4
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


def agreement_reference():
    return f"EA-{uuid4().hex.upper()}"


class ExchangeAgreement(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        CLOSED = "CLOSED", "Closed"

    reference = models.CharField(max_length=60, unique=True, default=agreement_reference)
    supplier = models.ForeignKey("suppliers.Supplier", on_delete=models.PROTECT, related_name="exchange_agreements")
    contract = models.ForeignKey("contracts.Contract", on_delete=models.PROTECT, null=True, blank=True, related_name="exchange_agreements")
    purchase_order = models.ForeignKey("contracts.PurchaseOrder", on_delete=models.PROTECT, null=True, blank=True, related_name="exchange_agreements")
    internal_policy = models.ForeignKey("contracts.InternalPolicy", on_delete=models.PROTECT, null=True, blank=True, related_name="exchange_agreements")
    responsible_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="assigned_agreements")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_agreements")
    created_at = models.DateTimeField(auto_now_add=True)
    start_date = models.DateField(default=timezone.localdate)
    deadline = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=6, choices=Status.choices, default=Status.OPEN)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["purchase_order"], name="one_agreement_per_po"),
            models.CheckConstraint(condition=Q(contract__isnull=False) | Q(purchase_order__isnull=False), name="agreement_requires_contract_or_po"),
            models.CheckConstraint(condition=Q(status__in=["OPEN", "CLOSED"]), name="agreement_valid_status"),
        ]

    def clean(self):
        errors = {}
        if not self.contract_id and not self.purchase_order_id:
            errors["contract"] = "Select a contract, a purchase order, or both."
        if self.contract_id and self.supplier_id and self.contract.supplier_id != self.supplier_id:
            errors["contract"] = "The contract must belong to the selected supplier."
        if self.purchase_order_id:
            po = self.purchase_order
            if self.supplier_id and po.supplier_id != self.supplier_id:
                errors["purchase_order"] = "The purchase order must belong to the selected supplier."
            if self.contract_id and po.contract_id and po.contract_id != self.contract_id:
                errors["contract"] = "The contract must match the purchase order's contract."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.reference
