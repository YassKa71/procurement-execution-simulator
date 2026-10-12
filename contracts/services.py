from django.core.exceptions import ValidationError
from django.db import transaction
from accounts.services import require_permission
from agreements.services import _create_agreement
from audit.models import BusinessEvent
from audit.services import record_event
from .models import Contract, InternalPolicy, PurchaseOrder


FIELDS = {
    Contract: ("reference", "supplier", "title", "start_date", "end_date", "renewable", "document"),
    InternalPolicy: ("reference", "version", "title", "document"),
    PurchaseOrder: ("reference", "supplier", "contract", "order_date", "requested_delivery", "description", "document"),
}
PERMISSIONS = {Contract: "accounts.manage_contract", InternalPolicy: "accounts.manage_policy", PurchaseOrder: "accounts.manage_purchase_order"}
EVENT_PREFIX = {Contract: "CONTRACT", InternalPolicy: "POLICY", PurchaseOrder: "PURCHASE_ORDER"}


def snapshot(record):
    """Persist identifying data and document storage keys even after deletion."""
    result = {}
    for field in record._meta.concrete_fields:
        value = getattr(record, field.attname)
        result[field.name] = value if value is None or isinstance(value, (str, int, bool)) else str(value)
    return result


def _save_with_event(record, *, actor, event_type, before=None):
    record.full_clean()
    record.save()
    record_event(actor=actor, event_type=event_type, related_object=record,
                 summary=f"{record._meta.verbose_name.capitalize()} {record.reference} {event_type.rsplit('_', 1)[1].lower()}.",
                 metadata={"before": before, "after": snapshot(record)})


def _cleanup_failed_upload(record, original_name):
    if record.document and record.document._committed and record.document.name != original_name:
        record.document.delete(save=False)


def create_contract(*, actor, reference, supplier, title, start_date, end_date=None, renewable=False, document=None):
    require_permission(actor, "accounts.create_contract")
    record = Contract(reference=reference, supplier=supplier, title=title, start_date=start_date,
                      end_date=end_date, renewable=renewable, document=document or "", created_by=actor)
    try:
        with transaction.atomic():
            _save_with_event(record, actor=actor, event_type="CONTRACT_CREATED")
    except Exception:
        _cleanup_failed_upload(record, "")
        raise
    return record


def create_internal_policy(*, actor, reference, version, title, document):
    require_permission(actor, "accounts.manage_policy")
    record = InternalPolicy(reference=reference, version=version, title=title, document=document)
    try:
        with transaction.atomic():
            _save_with_event(record, actor=actor, event_type="POLICY_CREATED")
    except Exception:
        _cleanup_failed_upload(record, "")
        raise
    return record


def create_purchase_order(*, actor, reference, supplier, description, order_date, contract=None,
                          requested_delivery=None, document=None, internal_policy=None, create_agreement=False):
    require_permission(actor, "accounts.create_purchase_order")
    if create_agreement:
        require_permission(actor, "accounts.create_exchange_agreement")
    elif internal_policy is not None:
        raise ValidationError({"internal_policy": "Select a policy only when creating an associated agreement."})
    po = PurchaseOrder(reference=reference, supplier=supplier, description=description,
                       order_date=order_date, requested_delivery=requested_delivery,
                       document=document or "", created_by=actor)
    try:
        with transaction.atomic():
            if contract is not None:
                contract = Contract.objects.select_for_update().get(pk=contract.pk)
            po.contract = contract
            po.full_clean()
            po.save()
            agreement = None
            if create_agreement:
                agreement = _create_agreement(actor=actor, supplier=supplier, contract=contract,
                                               purchase_order=po, internal_policy=internal_policy,
                                               start_date=order_date, deadline=requested_delivery)
            record_event(actor=actor, event_type=BusinessEvent.EventType.PURCHASE_ORDER_CREATED,
                         related_object=po, agreement=agreement, metadata={"after": snapshot(po)},
                         summary=f"Purchase order {po.reference} created.")
    except Exception:
        _cleanup_failed_upload(po, "")
        raise
    return po, agreement


def _update_unused(*, actor, model, pk, changes):
    require_permission(actor, PERMISSIONS[model])
    if set(changes) - set(FIELDS[model]):
        raise ValidationError("Unsupported field change.")
    record = None
    original_name = ""
    try:
        with transaction.atomic():
            record = model.objects.select_for_update().get(pk=pk)
            original_name = record.document.name
            if record.is_used:
                raise ValidationError("This record is used by an open or closed agreement and cannot be modified.")
            before = snapshot(record)
            if model is Contract and "supplier" in changes and changes["supplier"].pk != record.supplier_id and record.purchase_orders.exists():
                raise ValidationError({"supplier": "Unlink existing POs before changing the contract supplier."})
            if model is PurchaseOrder and changes.get("contract") is not None:
                changes["contract"] = Contract.objects.select_for_update().get(pk=changes["contract"].pk)
            for field, value in changes.items():
                if field == "document":
                    if value is None or getattr(value, "_committed", False):
                        continue
                    value = value or ""
                setattr(record, field, value)
            _save_with_event(record, actor=actor, event_type=EVENT_PREFIX[model] + "_UPDATED", before=before)
    except Exception:
        if record is not None:
            _cleanup_failed_upload(record, original_name)
        raise
    return record


@transaction.atomic
def _delete_unused(*, actor, model, pk):
    require_permission(actor, PERMISSIONS[model])
    record = model.objects.select_for_update().get(pk=pk)
    if record.is_used:
        raise ValidationError("This record is used by an open or closed agreement and cannot be deleted.")
    if model is Contract and record.purchase_orders.exists():
        raise ValidationError("Unlink this contract from its purchase orders before deleting it.")
    record_event(actor=actor, event_type=EVENT_PREFIX[model] + "_DELETED", related_object=record,
                 summary=f"{record._meta.verbose_name.capitalize()} {record.reference} deleted.",
                 metadata={"before": snapshot(record)})
    record.delete()
    # Retain stored documents referenced by audit snapshots.


def update_contract(*, actor, pk, **changes):
    return _update_unused(actor=actor, model=Contract, pk=pk, changes=changes)


def delete_contract(*, actor, pk):
    return _delete_unused(actor=actor, model=Contract, pk=pk)


def update_internal_policy(*, actor, pk, **changes):
    return _update_unused(actor=actor, model=InternalPolicy, pk=pk, changes=changes)


def delete_internal_policy(*, actor, pk):
    return _delete_unused(actor=actor, model=InternalPolicy, pk=pk)


def update_purchase_order(*, actor, pk, **changes):
    return _update_unused(actor=actor, model=PurchaseOrder, pk=pk, changes=changes)


def delete_purchase_order(*, actor, pk):
    return _delete_unused(actor=actor, model=PurchaseOrder, pk=pk)
