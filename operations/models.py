from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator, RegexValidator, validate_email
from django.db import models
from django.db.models import Q
from django.utils import timezone

from contracts.models import resource_path


def validate_recipients(value):
    recipients = [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]
    if not recipients:
        raise ValidationError("Enter at least one recipient email address.")
    for recipient in recipients:
        validate_email(recipient)


class CaseResource(models.Model):
    exchange_agreement = models.ForeignKey("agreements.ExchangeAgreement", on_delete=models.PROTECT, related_name="%(class)ss")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        ordering = ["-created_at", "-pk"]


class GoodsReceipt(CaseResource):
    description = models.CharField(max_length=250)
    quantity = models.DecimalField(max_digits=16, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])
    unit = models.CharField(max_length=40, blank=True)
    reception_date = models.DateField(default=timezone.localdate)
    comment = models.TextField(blank=True)
    document = models.FileField(upload_to=resource_path, blank=True)
    is_validated = models.BooleanField(default=False)
    valid_goods_percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
                                                 validators=[MinValueValidator(0), MaxValueValidator(100)])
    assessment_note = models.TextField(blank=True)
    assessed_at = models.DateTimeField(null=True, blank=True)
    assessed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="goods_assessments")

    class Meta(CaseResource.Meta):
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="receipt_positive_quantity"),
            models.CheckConstraint(
                condition=(Q(is_validated=False, valid_goods_percentage__isnull=True, assessed_at__isnull=True, assessed_by__isnull=True)
                           | Q(is_validated=True, valid_goods_percentage__isnull=False, valid_goods_percentage__gte=0,
                               valid_goods_percentage__lte=100, assessed_at__isnull=False, assessed_by__isnull=False)),
                name="receipt_assessment_consistent"),
        ]

    def __str__(self):
        return f"Receipt #{self.pk}: {self.description}"


class Requirement(CaseResource):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    note = models.TextField()
    deadline = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta(CaseResource.Meta):
        constraints = [models.CheckConstraint(
            condition=Q(status="OPEN", resolved_at__isnull=True) | Q(status__in=["COMPLETED", "CANCELLED"], resolved_at__isnull=False),
            name="requirement_resolution_consistent")]

    def __str__(self):
        return f"Requirement #{self.pk}: {self.note[:80]}"


class ValidationRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        CANCELLED = "CANCELLED", "Cancelled"

    class EmailStatus(models.TextChoices):
        NOT_SENT = "NOT_SENT", "Not sent"
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"

    requirement = models.OneToOneField(Requirement, on_delete=models.PROTECT, related_name="validation_request")
    person = models.CharField(max_length=200)
    email = models.EmailField()
    question = models.TextField()
    request_date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    email_status = models.CharField(max_length=12, choices=EmailStatus.choices, default=EmailStatus.NOT_SENT)
    response_date = models.DateField(null=True, blank=True)
    responder = models.CharField(max_length=200, blank=True)
    response_comment = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    document = models.FileField(upload_to=resource_path, blank=True)

    class Meta:
        ordering = ["-requirement__created_at", "-pk"]
        constraints = [models.CheckConstraint(
            condition=(Q(status__in=["PENDING", "CANCELLED"], response_date__isnull=True, recorded_by__isnull=True)
                       | Q(status__in=["APPROVED", "REJECTED"], response_date__isnull=False, recorded_by__isnull=False) & ~Q(responder="")),
            name="validation_response_consistent")]

    def clean(self):
        if self.status in (self.Status.APPROVED, self.Status.REJECTED):
            if not self.responder.strip():
                raise ValidationError({"responder": "Identify the person who responded."})
            if self.response_date and self.response_date < self.request_date:
                raise ValidationError({"response_date": "The response cannot precede the request."})

    def __str__(self):
        return f"Validation #{self.pk} - {self.person}"


class CaseDocument(CaseResource):
    title = models.CharField(max_length=200)
    category = models.CharField(max_length=100)
    date = models.DateField(default=timezone.localdate)
    notes = models.TextField(blank=True)
    document = models.FileField(upload_to=resource_path)

    def __str__(self):
        return self.title


class CaseEmail(CaseResource):
    class Direction(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        SENT = "SENT", "Sent"

    sender = models.EmailField()
    recipients = models.TextField(validators=[validate_recipients], help_text="Email addresses separated by commas.")
    subject = models.CharField(max_length=250)
    occurred_at = models.DateTimeField(default=timezone.now)
    direction = models.CharField(max_length=8, choices=Direction.choices, default=Direction.RECEIVED)
    body = models.TextField(help_text="Original email body or a summary.")
    document = models.FileField(upload_to=resource_path, blank=True, verbose_name="Original email or attachment")

    def __str__(self):
        return self.subject


class SupplierInvoice(CaseResource):
    reference = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    currency = models.CharField(max_length=3, validators=[RegexValidator(r"^[A-Z]{3}$", "Use a three-letter uppercase currency code, e.g. EUR.")])
    invoice_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(null=True, blank=True)
    document = models.FileField(upload_to=resource_path)
    notes = models.TextField(blank=True)

    class Meta(CaseResource.Meta):
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="supplier_invoice_positive_amount")]

    def __str__(self):
        return self.reference
