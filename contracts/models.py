from pathlib import PurePosixPath
from uuid import uuid4
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


def resource_path(instance, filename):
    return f"resources/{uuid4().hex}{PurePosixPath(filename).suffix.lower()}"


class Contract(models.Model):
    reference = models.CharField(max_length=60, unique=True)
    supplier = models.ForeignKey("suppliers.Supplier", on_delete=models.PROTECT, related_name="contracts")
    title = models.CharField(max_length=200)
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    renewable = models.BooleanField(default=False)
    document = models.FileField(upload_to=resource_path, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [models.CheckConstraint(condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")), name="contract_dates_ordered")]

    @property
    def is_used(self):
        return self.exchange_agreements.exists() or self.purchase_orders.filter(exchange_agreements__isnull=False).exists()

    @property
    def lifecycle(self):
        today = timezone.localdate()
        if self.start_date > today:
            return "Upcoming"
        if self.end_date and self.end_date < today:
            return "Expired"
        return "Active"

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date cannot precede the start date."})

    def __str__(self):
        return self.reference


class InternalPolicy(models.Model):
    reference = models.CharField(max_length=60)
    version = models.CharField(max_length=40)
    title = models.CharField(max_length=200)
    document = models.FileField(upload_to=resource_path)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["reference", "-created_at"]
        constraints = [models.UniqueConstraint(fields=["reference", "version"], name="unique_policy_version")]

    @property
    def is_used(self):
        return self.exchange_agreements.exists()

    def __str__(self):
        return f"{self.reference} (v{self.version})"


class PurchaseOrder(models.Model):
    reference = models.CharField(max_length=60, unique=True)
    supplier = models.ForeignKey("suppliers.Supplier", on_delete=models.PROTECT, related_name="purchase_orders")
    contract = models.ForeignKey(Contract, on_delete=models.PROTECT, null=True, blank=True, related_name="purchase_orders")
    order_date = models.DateField(default=timezone.localdate)
    requested_delivery = models.DateField(null=True, blank=True)
    description = models.TextField(help_text="Describe goods, quantities, prices, currency and purchasing conditions.")
    document = models.FileField(upload_to=resource_path, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        ordering = ["-created_at", "-pk"]

    @property
    def is_used(self):
        return self.exchange_agreements.exists()

    def clean(self):
        if self.contract_id and self.supplier_id and self.contract.supplier_id != self.supplier_id:
            raise ValidationError({"contract": "The contract must belong to the selected supplier."})

    def __str__(self):
        return self.reference
