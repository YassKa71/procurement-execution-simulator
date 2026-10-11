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
