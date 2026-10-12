from django.conf import settings
from django.db import models


class BusinessEvent(models.Model):
    class EventType(models.TextChoices):
        CONTRACT_CREATED = "CONTRACT_CREATED", "Contract created"
        PURCHASE_ORDER_CREATED = "PURCHASE_ORDER_CREATED", "Purchase order created"
        EXCHANGE_AGREEMENT_CREATED = "EXCHANGE_AGREEMENT_CREATED", "Exchange agreement created"

        CONTRACT_UPDATED = "CONTRACT_UPDATED", "Contract updated"
        CONTRACT_DELETED = "CONTRACT_DELETED", "Contract deleted"
        POLICY_CREATED = "POLICY_CREATED", "Policy created"
        POLICY_UPDATED = "POLICY_UPDATED", "Policy updated"
        POLICY_DELETED = "POLICY_DELETED", "Policy deleted"
        PURCHASE_ORDER_UPDATED = "PURCHASE_ORDER_UPDATED", "Purchase order updated"
        PURCHASE_ORDER_DELETED = "PURCHASE_ORDER_DELETED", "Purchase order deleted"

        GOODS_RECEIVED = "GOODS_RECEIVED", "Goods received"
        GOODS_ASSESSED = "GOODS_ASSESSED", "Goods assessed"
        GOODS_ASSESSMENT_CORRECTED = "GOODS_ASSESSMENT_CORRECTED", "Goods assessment corrected"
        REQUIREMENT_CREATED = "REQUIREMENT_CREATED", "Requirement created"
        REQUIREMENT_UPDATED = "REQUIREMENT_UPDATED", "Requirement updated"
        REQUIREMENT_COMPLETED = "REQUIREMENT_COMPLETED", "Requirement completed"
        REQUIREMENT_CANCELLED = "REQUIREMENT_CANCELLED", "Requirement cancelled"
        VALIDATION_REQUESTED = "VALIDATION_REQUESTED", "Validation requested"
        VALIDATION_RECEIVED = "VALIDATION_RECEIVED", "Validation received"
        VALIDATION_CANCELLED = "VALIDATION_CANCELLED", "Validation cancelled"
        VALIDATION_EMAIL_SENT = "VALIDATION_EMAIL_SENT", "Validation email sent"
        VALIDATION_EMAIL_FAILED = "VALIDATION_EMAIL_FAILED", "Validation email failed"
        DOCUMENT_ATTACHED = "DOCUMENT_ATTACHED", "Document attached"
        EMAIL_ATTACHED = "EMAIL_ATTACHED", "Email attached"
        SUPPLIER_INVOICE_ATTACHED = "SUPPLIER_INVOICE_ATTACHED", "Supplier invoice attached"

        DIGITAL_INVOICE_CREATED = "DIGITAL_INVOICE_CREATED", "Digital invoice created"
        DIGITAL_INVOICE_UPDATED = "DIGITAL_INVOICE_UPDATED", "Digital invoice updated"
        DIGITAL_INVOICE_READY = "DIGITAL_INVOICE_READY", "Digital invoice ready"
        DIGITAL_INVOICE_DRAFTED = "DIGITAL_INVOICE_DRAFTED", "Digital invoice drafted"
        DIGITAL_INVOICE_CANCELLED = "DIGITAL_INVOICE_CANCELLED", "Digital invoice cancelled"
        DIGITAL_INVOICE_PAID = "DIGITAL_INVOICE_PAID", "Digital invoice paid"
        PAYMENT_EXECUTED = "PAYMENT_EXECUTED", "Payment executed"
        EXCHANGE_AGREEMENT_CLOSED = "EXCHANGE_AGREEMENT_CLOSED", "Exchange agreement closed"
        EXCHANGE_AGREEMENT_REOPENED = "EXCHANGE_AGREEMENT_REOPENED", "Exchange agreement reopened"

    class ActorType(models.TextChoices):
        USER = "USER", "User"
        SYSTEM = "SYSTEM", "System"

    exchange_agreement = models.ForeignKey("agreements.ExchangeAgreement", on_delete=models.PROTECT, null=True, blank=True, related_name="events")
    timestamp = models.DateTimeField(auto_now_add=True)
    event_type = models.CharField(max_length=60, choices=EventType.choices)
    actor_type = models.CharField(max_length=20, choices=ActorType.choices)
    actor_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    actor_label = models.CharField(max_length=200)
    related_object_type = models.CharField(max_length=100)
    related_object_id = models.PositiveBigIntegerField()
    summary = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["timestamp", "pk"]

    def __str__(self):
        return self.summary
