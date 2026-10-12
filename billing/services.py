from contextlib import contextmanager
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from accounts.services import require_permission
from agreements.models import ExchangeAgreement
from audit.services import record_event
from .models import DigitalInvoice, Payment


EDITABLE_FIELDS = ("description", "calculation", "amount", "currency", "due_date")


def _snapshot(invoice):
    fields = (*EDITABLE_FIELDS, "status", "revision", "paid_at", "exchange_agreement_id")
    return {field: str(getattr(invoice, field)) if getattr(invoice, field) is not None else None for field in fields}


@contextmanager
def _action(actor, agreement_id, permission):
    require_permission(actor, "accounts." + permission)
    with transaction.atomic():
        # Every operational and billing action locks the agreement first.
        agreement = ExchangeAgreement.objects.select_for_update().get(pk=agreement_id)
        if agreement.status != "OPEN":
            raise ValidationError("This agreement is closed. Reopen it before changing invoices or executing payments.")
        yield agreement


def _invoice(agreement, invoice_id, expected_revision):
    try:
        invoice = DigitalInvoice.objects.select_for_update().get(pk=invoice_id, exchange_agreement=agreement)
    except DigitalInvoice.DoesNotExist:
        raise ValidationError("The invoice does not belong to this agreement.")
    if invoice.revision != expected_revision:
        raise ValidationError("This invoice changed since you opened the form. Reload it and review the current details.")
    return invoice


def _save(invoice, actor, event_type, summary, before=None):
    invoice.full_clean()
    invoice.save()
    record_event(actor=actor, agreement=invoice.exchange_agreement, related_object=invoice,
                 event_type=event_type, summary=summary, metadata={"before": before, "after": _snapshot(invoice)})
    return invoice


def create_digital_invoice(*, actor, agreement_id, description, calculation, amount, currency, due_date):
    with _action(actor, agreement_id, "manage_digital_invoice") as agreement:
        invoice = DigitalInvoice(exchange_agreement=agreement, created_by=actor, description=description,
                                 calculation=calculation, amount=amount, currency=currency, due_date=due_date)
        return _save(invoice, actor, "DIGITAL_INVOICE_CREATED", "Digital invoice created as a draft.")


def update_digital_invoice(*, actor, agreement_id, invoice_id, expected_revision, description, calculation, amount, currency, due_date):
    with _action(actor, agreement_id, "manage_digital_invoice") as agreement:
        invoice = _invoice(agreement, invoice_id, expected_revision)
        if invoice.status not in ("DRAFT", "READY_TO_PAY"):
            raise ValidationError("Paid and cancelled invoices cannot be edited.")
        before = _snapshot(invoice)
        for field, value in dict(description=description, calculation=calculation, amount=amount, currency=currency, due_date=due_date).items():
            setattr(invoice, field, value)
        invoice.full_clean()
        changed = any(before[field] != str(getattr(invoice, field)) for field in EDITABLE_FIELDS)
        if not changed:
            return invoice
        invoice.status = "DRAFT"
        invoice.revision += 1
        return _save(invoice, actor, "DIGITAL_INVOICE_UPDATED", "Digital invoice revised; readiness must be confirmed again.", before)


TRANSITIONS = {
    "ready": ("mark_invoice_ready", ("DRAFT",), "READY_TO_PAY", "DIGITAL_INVOICE_READY"),
    "draft": ("manage_digital_invoice", ("READY_TO_PAY",), "DRAFT", "DIGITAL_INVOICE_DRAFTED"),
    "cancel": ("manage_digital_invoice", ("DRAFT", "READY_TO_PAY"), "CANCELLED", "DIGITAL_INVOICE_CANCELLED"),
}


def transition_invoice(*, actor, agreement_id, invoice_id, expected_revision, action):
    if action not in TRANSITIONS:
        raise ValidationError("Unknown invoice action.")
    permission, allowed, target, event = TRANSITIONS[action]
    with _action(actor, agreement_id, permission) as agreement:
        invoice = _invoice(agreement, invoice_id, expected_revision)
        if invoice.status not in allowed:
            raise ValidationError("This action is unavailable for the current invoice status.")
        before = _snapshot(invoice)
        invoice.status, invoice.revision = target, invoice.revision + 1
        return _save(invoice, actor, event, f"Digital invoice {invoice.reference}: {invoice.get_status_display()}.", before)


def execute_payment(*, actor, agreement_id, invoice_id, expected_revision):
    """Simulate payment atomically. No external bank or transfer is invoked."""
    with _action(actor, agreement_id, "execute_payment") as agreement:
        invoice = _invoice(agreement, invoice_id, expected_revision)
        if invoice.status != "READY_TO_PAY":
            raise ValidationError("Only a ready-to-pay invoice can be paid.")
        before = _snapshot(invoice)
        paid_at = timezone.now()
        payment = Payment(invoice=invoice, amount=invoice.amount, currency=invoice.currency, executed_by=actor, executed_at=paid_at)
        payment.full_clean()
        payment.save()
        invoice.status, invoice.paid_at, invoice.revision = "PAID", paid_at, invoice.revision + 1
        _save(invoice, actor, "DIGITAL_INVOICE_PAID", f"Simulated payment {payment.reference}: {payment.amount} {payment.currency}.", before)
        record_event(actor=actor, agreement=agreement, related_object=payment, event_type="PAYMENT_EXECUTED",
                     summary=f"Simulated payment recorded for {invoice.reference}.",
                     metadata={"reference": payment.reference, "amount": str(payment.amount), "currency": payment.currency, "invoice_id": invoice.pk})
        return payment
