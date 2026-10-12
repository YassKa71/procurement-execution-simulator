from uuid import uuid4
from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


def invoice_reference():
    return f"DI-{uuid4().hex.upper()}"


def payment_reference():
    return f"SIM-{uuid4().hex.upper()}"


class DigitalInvoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        READY_TO_PAY = "READY_TO_PAY", "Ready to pay"
        PAID = "PAID", "Paid"
        CANCELLED = "CANCELLED", "Cancelled"

    reference = models.CharField(max_length=40, unique=True, default=invoice_reference)
    exchange_agreement = models.ForeignKey("agreements.ExchangeAgreement", on_delete=models.PROTECT, related_name="digital_invoices")
    description = models.TextField(help_text="Describe the goods, services or portion of the exchange covered.")
    calculation = models.TextField(help_text="Explain how the amount was determined, including adjustments or partial delivery.")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3, validators=[RegexValidator(r"^[A-Z]{3}$", "Use a three-letter uppercase currency code.")])
    due_date = models.DateField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_digital_invoices")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="digital_invoice_positive_amount"),
            models.CheckConstraint(condition=Q(status__in=["DRAFT", "READY_TO_PAY", "PAID", "CANCELLED"]), name="digital_invoice_valid_status"),
            models.CheckConstraint(condition=(Q(status="PAID", paid_at__isnull=False) | (~Q(status="PAID") & Q(paid_at__isnull=True))), name="digital_invoice_paid_timestamp"),
        ]

    @property
    def is_overdue(self):
        return self.status in (self.Status.DRAFT, self.Status.READY_TO_PAY) and self.due_date < timezone.localdate()

    def __str__(self):
        return self.reference


class Payment(models.Model):
    """An immutable simulated payment, with the financial values captured at execution."""
    invoice = models.OneToOneField(DigitalInvoice, on_delete=models.PROTECT, related_name="payment")
    reference = models.CharField(max_length=40, unique=True, default=payment_reference)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3)
    executed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    executed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-executed_at", "-pk"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="payment_positive_amount")]

    def __str__(self):
        return self.reference
