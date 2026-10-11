from contextlib import contextmanager
import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.services import require_permission
from agreements.models import ExchangeAgreement
from audit.services import record_event
from . import messaging
from .models import GoodsReceipt, Requirement, ValidationRequest, CaseDocument, CaseEmail, SupplierInvoice

logger = logging.getLogger(__name__)


def _snapshot(record):
    data = {}
    for field in record._meta.concrete_fields:
        value = getattr(record, field.attname)
        data[field.name] = value if value is None or isinstance(value, (str, int, bool)) else str(value)
    return data


@contextmanager
def _case_action(actor, agreement_id):
    require_permission(actor, "accounts.operate_exchange_agreement")
    uploads = []
    try:
        with transaction.atomic():
            agreement = ExchangeAgreement.objects.select_for_update().get(pk=agreement_id)
            if agreement.status != ExchangeAgreement.Status.OPEN:
                raise ValidationError("This agreement is closed. Reopen it before making operational changes.")
            yield agreement, uploads
    except Exception:
        for upload in uploads:
            if upload.name and upload._committed:
                try:
                    upload.delete(save=False)
                except Exception:
                    logger.exception("Failed to remove an upload after a rolled-back operation")
        raise


def _save(record, *, actor, agreement, uploads, event_type, summary, before=None):
    record.full_clean()
    document = getattr(record, "document", None)
    if document and not document._committed:
        uploads.append(document)
    record.save()
    record_event(actor=actor, agreement=agreement, related_object=record, event_type=event_type,
                 summary=summary, metadata={"before": before, "after": _snapshot(record)})
    return record


def _resource(model, pk, agreement):
    try:
        return model.objects.select_for_update().get(pk=pk, exchange_agreement=agreement)
    except model.DoesNotExist:
        raise ValidationError("The selected record does not belong to this agreement.")


def _pending_requirement(pk, agreement, *, allow_validation=False):
    requirement = _resource(Requirement, pk, agreement)
    if requirement.status != Requirement.Status.OPEN:
        raise ValidationError("This requirement has already been resolved.")
    if not allow_validation and ValidationRequest.objects.filter(requirement=requirement).exists():
        raise ValidationError("Use the validation response or cancellation action for this requirement.")
    return requirement


def declare_goods_received(*, actor, agreement_id, description, quantity, reception_date, unit="", comment="", document=None):
    with _case_action(actor, agreement_id) as (agreement, uploads):
        receipt = GoodsReceipt(exchange_agreement=agreement, created_by=actor, description=description,
                               quantity=quantity, reception_date=reception_date, unit=unit, comment=comment,
                               document=document or "")
        return _save(receipt, actor=actor, agreement=agreement, uploads=uploads, event_type="GOODS_RECEIVED",
                     summary=f"Goods received: {quantity} {unit} of {description}.")


def assess_goods_receipt(*, actor, agreement_id, receipt_id, valid_goods_percentage, assessment_note=""):
    with _case_action(actor, agreement_id) as (agreement, uploads):
        receipt = _resource(GoodsReceipt, receipt_id, agreement)
        before = _snapshot(receipt)
        correcting = receipt.is_validated
        if correcting and not assessment_note.strip():
            raise ValidationError({"assessment_note": "Explain why the previous assessment is being corrected."})
        receipt.is_validated = True
        receipt.valid_goods_percentage = valid_goods_percentage
        receipt.assessment_note = assessment_note
        receipt.assessed_at = timezone.now()
        receipt.assessed_by = actor
        return _save(receipt, actor=actor, agreement=agreement, uploads=uploads, before=before,
                     event_type="GOODS_ASSESSMENT_CORRECTED" if correcting else "GOODS_ASSESSED",
                     summary=f"Receipt #{receipt.pk} assessed: {valid_goods_percentage}% valid goods.")


def create_requirement(*, actor, agreement_id, note, deadline=None):
    with _case_action(actor, agreement_id) as (agreement, uploads):
        requirement = Requirement(exchange_agreement=agreement, created_by=actor, note=note, deadline=deadline)
        return _save(requirement, actor=actor, agreement=agreement, uploads=uploads, event_type="REQUIREMENT_CREATED",
                     summary=f"Requirement created: {note[:160]}")


def update_requirement(*, actor, agreement_id, requirement_id, note, deadline=None):
    with _case_action(actor, agreement_id) as (agreement, uploads):
        requirement = _pending_requirement(requirement_id, agreement, allow_validation=True)
        before = _snapshot(requirement)
        requirement.note, requirement.deadline = note, deadline
        return _save(requirement, actor=actor, agreement=agreement, uploads=uploads, before=before,
                     event_type="REQUIREMENT_UPDATED", summary=f"Requirement #{requirement.pk} note or deadline updated.")


def _finish_requirement(requirement, status, actor, agreement, uploads):
    before = _snapshot(requirement)
    requirement.status = status
    requirement.resolved_at = timezone.now()
    return _save(requirement, actor=actor, agreement=agreement, uploads=uploads, before=before,
                 event_type=f"REQUIREMENT_{status}", summary=f"Requirement #{requirement.pk} {status.lower()}.")


def complete_requirement(*, actor, agreement_id, requirement_id):
    with _case_action(actor, agreement_id) as (agreement, uploads):
        requirement = _pending_requirement(requirement_id, agreement)
        return _finish_requirement(requirement, Requirement.Status.COMPLETED, actor, agreement, uploads)


def cancel_requirement(*, actor, agreement_id, requirement_id):
    with _case_action(actor, agreement_id) as (agreement, uploads):
        requirement = _pending_requirement(requirement_id, agreement)
        return _finish_requirement(requirement, Requirement.Status.CANCELLED, actor, agreement, uploads)


def request_validation(*, actor, agreement_id, person, email, question, request_date,
                       note="", deadline=None, send_email=False):
    with _case_action(actor, agreement_id) as (agreement, uploads):
        requirement = Requirement(exchange_agreement=agreement, created_by=actor, note=note or question, deadline=deadline)
        _save(requirement, actor=actor, agreement=agreement, uploads=uploads, event_type="REQUIREMENT_CREATED",
              summary=f"Validation requirement created: {requirement.note[:160]}")
        validation = ValidationRequest(requirement=requirement, person=person, email=email, question=question, request_date=request_date)
        _save(validation, actor=actor, agreement=agreement, uploads=uploads, event_type="VALIDATION_REQUESTED",
              summary=f"Validation requested from {person}: {question[:160]}")
        if send_email:
            transaction.on_commit(lambda: _deliver_validation_email(actor, agreement_id, validation.pk), robust=True)
        return validation


def _pending_validation(validation_id, agreement):
    try:
        validation = ValidationRequest.objects.select_for_update().get(pk=validation_id, requirement__exchange_agreement=agreement)
    except ValidationRequest.DoesNotExist:
        raise ValidationError("Select an existing validation request from this agreement.")
    requirement = _pending_requirement(validation.requirement_id, agreement, allow_validation=True)
    if validation.status != ValidationRequest.Status.PENDING:
        raise ValidationError("This validation request already has a decision or was cancelled.")
    return validation, requirement


def record_validation(*, actor, agreement_id, validation_id, decision, response_date, responder,
                      response_comment="", document=None):
    with _case_action(actor, agreement_id) as (agreement, uploads):
        validation, requirement = _pending_validation(validation_id, agreement)
        if decision not in (ValidationRequest.Status.APPROVED, ValidationRequest.Status.REJECTED):
            raise ValidationError({"decision": "Choose approved or rejected."})
        before = _snapshot(validation)
        validation.status = decision
        validation.response_date = response_date
        validation.responder = responder
        validation.response_comment = response_comment
        validation.recorded_by = actor
        validation.document = document or ""
        _save(validation, actor=actor, agreement=agreement, uploads=uploads, before=before,
              event_type="VALIDATION_RECEIVED", summary=f"Validation #{validation.pk} {decision.lower()} by {responder}.")
        _finish_requirement(requirement, Requirement.Status.COMPLETED, actor, agreement, uploads)
        return validation


def cancel_validation(*, actor, agreement_id, validation_id):
    with _case_action(actor, agreement_id) as (agreement, uploads):
        validation, requirement = _pending_validation(validation_id, agreement)
        before = _snapshot(validation)
        validation.status = ValidationRequest.Status.CANCELLED
        _save(validation, actor=actor, agreement=agreement, uploads=uploads, before=before,
              event_type="VALIDATION_CANCELLED", summary=f"Validation #{validation.pk} cancelled.")
        _finish_requirement(requirement, Requirement.Status.CANCELLED, actor, agreement, uploads)
        return validation


def send_validation_request(*, actor, agreement_id, validation_id):
    with _case_action(actor, agreement_id) as (agreement, uploads):
        validation, _ = _pending_validation(validation_id, agreement)
        if validation.email_status == ValidationRequest.EmailStatus.SENT:
            raise ValidationError("This request email has already been sent.")
        transaction.on_commit(lambda: _deliver_validation_email(actor, agreement_id, validation.pk), robust=True)
        return validation


def _deliver_validation_email(actor, agreement_id, validation_id):
    # Runs only after the request commits. A failure preserves the request for a manual retry.
    with _case_action(actor, agreement_id) as (agreement, uploads):
        validation, _ = _pending_validation(validation_id, agreement)
        if validation.email_status == ValidationRequest.EmailStatus.SENT:
            return
        before = _snapshot(validation)
        try:
            messaging.send_validation_email(validation)
        except Exception:
            logger.exception("Validation email delivery failed for request %s", validation.pk)
            validation.email_status = ValidationRequest.EmailStatus.FAILED
            event_type, summary = "VALIDATION_EMAIL_FAILED", f"Email delivery failed for validation #{validation.pk}. Retry from the case."
        else:
            validation.email_status = ValidationRequest.EmailStatus.SENT
            event_type, summary = "VALIDATION_EMAIL_SENT", f"Validation #{validation.pk} email sent to {validation.email}."
        _save(validation, actor=actor, agreement=agreement, uploads=uploads, before=before, event_type=event_type, summary=summary)


def attach_document(*, actor, agreement_id, title, category, date, document, notes=""):
    with _case_action(actor, agreement_id) as (agreement, uploads):
        record = CaseDocument(exchange_agreement=agreement, created_by=actor, title=title, category=category,
                              date=date, document=document, notes=notes)
        return _save(record, actor=actor, agreement=agreement, uploads=uploads, event_type="DOCUMENT_ATTACHED",
                     summary=f"Document attached: {title}.")


def attach_email(*, actor, agreement_id, sender, recipients, subject, occurred_at, direction, body, document=None):
    with _case_action(actor, agreement_id) as (agreement, uploads):
        record = CaseEmail(exchange_agreement=agreement, created_by=actor, sender=sender, recipients=recipients,
                           subject=subject, occurred_at=occurred_at, direction=direction, body=body, document=document or "")
        return _save(record, actor=actor, agreement=agreement, uploads=uploads, event_type="EMAIL_ATTACHED",
                     summary=f"Email attached: {subject}.")


def attach_supplier_invoice(*, actor, agreement_id, reference, amount, currency, invoice_date, document,
                            due_date=None, notes=""):
    with _case_action(actor, agreement_id) as (agreement, uploads):
        record = SupplierInvoice(exchange_agreement=agreement, created_by=actor, reference=reference, amount=amount,
                                 currency=currency, invoice_date=invoice_date, document=document, due_date=due_date, notes=notes)
        return _save(record, actor=actor, agreement=agreement, uploads=uploads, event_type="SUPPLIER_INVOICE_ATTACHED",
                     summary=f"Supplier invoice {reference} attached: {amount} {currency}.")
