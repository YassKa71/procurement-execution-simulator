from django.core.exceptions import ValidationError
from django.db import transaction
from accounts.services import require_permission
from audit.models import BusinessEvent
from audit.services import record_event
from contracts.models import Contract, InternalPolicy, PurchaseOrder
from .models import ExchangeAgreement


def _create_agreement(*, actor, supplier, contract=None, purchase_order=None, internal_policy=None,
                      responsible_user=None, start_date=None, deadline=None):
    """Caller owns authorization and transaction. Lock order: PO, contracts, policy."""
    if purchase_order is not None:
        purchase_order = PurchaseOrder.objects.select_for_update().get(pk=purchase_order.pk)
        if purchase_order.exchange_agreements.exists():
            raise ValidationError({"purchase_order": "This PO already has an Exchange Agreement, including closed agreements."})
    contract_ids = {contract.pk} if contract is not None else set()
    if purchase_order and purchase_order.contract_id:
        contract_ids.add(purchase_order.contract_id)
    locked_contracts = {item.pk: item for item in Contract.objects.select_for_update().filter(pk__in=contract_ids).order_by("pk")}
    if contract is not None:
        if contract.pk not in locked_contracts:
            raise ValidationError({"contract": "The selected contract no longer exists."})
        contract = locked_contracts[contract.pk]
    if internal_policy is not None:
        internal_policy = InternalPolicy.objects.select_for_update().get(pk=internal_policy.pk)
    agreement = ExchangeAgreement(
        supplier=supplier, contract=contract, purchase_order=purchase_order,
        internal_policy=internal_policy, responsible_user=responsible_user or actor,
        created_by=actor, deadline=deadline,
    )
    if start_date is not None:
        agreement.start_date = start_date
    agreement.full_clean()
    agreement.save()
    record_event(actor=actor, event_type=BusinessEvent.EventType.EXCHANGE_AGREEMENT_CREATED,
                 related_object=agreement, agreement=agreement,
                 summary=f"Exchange agreement {agreement.reference} created.")
    return agreement


@transaction.atomic
def create_exchange_agreement(*, actor, supplier, contract=None, purchase_order=None,
                              internal_policy=None, responsible_user=None, start_date=None, deadline=None):
    require_permission(actor, "accounts.create_exchange_agreement")
    return _create_agreement(actor=actor, supplier=supplier, contract=contract,
                             purchase_order=purchase_order, internal_policy=internal_policy,
                             responsible_user=responsible_user, start_date=start_date, deadline=deadline)


@transaction.atomic
def close_exchange_agreement(*, actor, agreement_id, reason, note=""):
    from django.utils import timezone
    from operations.models import Requirement, ValidationRequest
    from operations.services import cancel_requirement, cancel_validation

    require_permission(actor, "accounts.operate_exchange_agreement")
    agreement = ExchangeAgreement.objects.select_for_update().get(pk=agreement_id)
    if agreement.status != "OPEN":
        raise ValidationError("This agreement is already closed.")
    if reason not in ExchangeAgreement.ClosureReason.values:
        raise ValidationError({"reason": "Choose why this exchange is closing."})
    if agreement.digital_invoices.filter(status="READY_TO_PAY").exists():
        raise ValidationError("Ready-to-pay invoices block closure. Pay, cancel or return them to draft first.")
    cancelled = []
    for requirement in Requirement.objects.filter(exchange_agreement=agreement, status="OPEN").order_by("pk"):
        validation = ValidationRequest.objects.filter(requirement=requirement).first()
        if validation:
            cancel_validation(actor=actor, agreement_id=agreement.pk, validation_id=validation.pk)
        else:
            cancel_requirement(actor=actor, agreement_id=agreement.pk, requirement_id=requirement.pk)
        cancelled.append(requirement.pk)
    agreement.status = "CLOSED"
    agreement.closed_at, agreement.closed_by = timezone.now(), actor
    agreement.closure_reason, agreement.closure_note = reason, note
    agreement.full_clean()
    agreement.save()
    record_event(actor=actor, agreement=agreement, related_object=agreement, event_type="EXCHANGE_AGREEMENT_CLOSED",
                 summary=f"Exchange agreement closed: {agreement.get_closure_reason_display()}.",
                 metadata={"reason": reason, "note": note, "cancelled_requirement_ids": cancelled, "closed_at": str(agreement.closed_at)})
    return agreement


@transaction.atomic
def reopen_exchange_agreement(*, actor, agreement_id, note=""):
    require_permission(actor, "accounts.operate_exchange_agreement")
    agreement = ExchangeAgreement.objects.select_for_update().get(pk=agreement_id)
    if agreement.status != "CLOSED":
        raise ValidationError("This agreement is already open.")
    previous = {"reason": agreement.closure_reason, "note": agreement.closure_note, "closed_at": str(agreement.closed_at), "closed_by_id": agreement.closed_by_id}
    agreement.status = "OPEN"
    agreement.closed_at = agreement.closed_by = None
    agreement.closure_reason = agreement.closure_note = ""
    agreement.full_clean()
    agreement.save()
    record_event(actor=actor, agreement=agreement, related_object=agreement, event_type="EXCHANGE_AGREEMENT_REOPENED",
                 summary="Exchange agreement reopened. Cancelled requirements remain cancelled.", metadata={"previous_closure": previous, "note": note})
    return agreement
