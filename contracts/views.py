from pathlib import PurePosixPath
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_safe, require_http_methods
from accounts.forms import add_service_errors
from accounts.services import require_permission
from .forms import ContractForm, PurchaseOrderForm, PurchaseOrderEditForm, InternalPolicyForm
from .models import Contract, InternalPolicy, PurchaseOrder
from .services import (create_contract, create_purchase_order, create_internal_policy,
                       update_contract, delete_contract, update_internal_policy,
                       delete_internal_policy, update_purchase_order, delete_purchase_order)


@login_required
@require_safe
def contract_list(request):
    require_permission(request.user, "accounts.access_workspace")
    records = Contract.objects.select_related("supplier")
    query = request.GET.get("q", "").strip()
    if query:
        records = records.filter(Q(reference__icontains=query) | Q(title__icontains=query) | Q(supplier__name__icontains=query))
    return render(request, "contracts/list.html", {"page_obj": Paginator(records, 25).get_page(request.GET.get("page")), "q": query})


@login_required
@require_http_methods(["GET", "POST"])
def contract_create(request):
    require_permission(request.user, "accounts.create_contract")
    form = ContractForm(request.POST if request.method == "POST" else None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            create_contract(actor=request.user, **form.cleaned_data)
        except ValidationError as error:
            add_service_errors(form, error)
        except IntegrityError:
            form.add_error(None, "This record conflicts with an existing record. Check its reference and try again.")
        else:
            messages.success(request, "Contract created.")
            return redirect("contract-list")
    return render(request, "record_form.html", {"form": form, "title": "Create contract"})


@login_required
@require_safe
def purchase_order_list(request):
    require_permission(request.user, "accounts.access_workspace")
    records = PurchaseOrder.objects.select_related("supplier", "contract").prefetch_related("exchange_agreements")
    query = request.GET.get("q", "").strip()
    if query:
        records = records.filter(Q(reference__icontains=query) | Q(supplier__name__icontains=query))
    return render(request, "contracts/po_list.html", {"page_obj": Paginator(records, 25).get_page(request.GET.get("page")), "q": query})


@login_required
@require_safe
def purchase_order_detail(request, pk):
    require_permission(request.user, "accounts.access_workspace")
    po = get_object_or_404(PurchaseOrder.objects.select_related("supplier", "contract"), pk=pk)
    return render(request, "contracts/po_detail.html", {"po": po})


@login_required
@require_http_methods(["GET", "POST"])
def purchase_order_create(request):
    require_permission(request.user, "accounts.create_purchase_order")
    form = PurchaseOrderForm(request.POST if request.method == "POST" else None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            po, agreement = create_purchase_order(actor=request.user, **form.cleaned_data)
        except ValidationError as error:
            add_service_errors(form, error)
        except IntegrityError:
            form.add_error(None, "This record conflicts with an existing record. Check its reference and try again.")
        else:
            if agreement is not None:
                messages.success(request, f"Purchase order {po.reference} and its exchange agreement created.")
                return redirect("agreement-detail", pk=agreement.pk)
            messages.success(request, f"Purchase order {po.reference} saved. No agreement was created.")
            return redirect("po-detail", pk=po.pk)
    return render(request, "record_form.html", {"form": form, "title": "Create purchase order", "explanation": "Save the PO independently, or choose Yes to create its agreement now."})


@login_required
@require_safe
def resource_download(request, kind, pk):
    require_permission(request.user, "accounts.access_workspace")
    model = {"contract": Contract, "purchase-order": PurchaseOrder, "policy": InternalPolicy}.get(kind)
    if model is None:
        raise Http404
    resource = get_object_or_404(model, pk=pk)
    if not resource.document:
        raise Http404("No document attached.")
    try:
        stream = resource.document.open("rb")
    except FileNotFoundError:
        raise Http404("Document unavailable.")
    response = FileResponse(stream, as_attachment=True, filename=PurePosixPath(resource.document.name).name, content_type="application/octet-stream")
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_safe
def policy_list(request):
    require_permission(request.user, "accounts.access_workspace")
    records = InternalPolicy.objects.all()
    return render(request, "contracts/policy_list.html", {"page_obj": Paginator(records, 25).get_page(request.GET.get("page"))})


@login_required
@require_http_methods(["GET", "POST"])
def policy_create(request):
    require_permission(request.user, "accounts.manage_policy")
    form = InternalPolicyForm(request.POST if request.method == "POST" else None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            create_internal_policy(actor=request.user, **form.cleaned_data)
        except ValidationError as error:
            add_service_errors(form, error)
        except IntegrityError:
            form.add_error(None, "This policy reference and version already exist.")
        else:
            messages.success(request, "Internal policy created.")
            return redirect("policy-list")
    return render(request, "record_form.html", {"form": form, "title": "Create internal policy"})


# All browser and admin maintenance routes call these same business services.
MAINTENANCE = {
    "contract": (Contract, ContractForm, "accounts.manage_contract", update_contract, delete_contract, "contract-list"),
    "policy": (InternalPolicy, InternalPolicyForm, "accounts.manage_policy", update_internal_policy, delete_internal_policy, "policy-list"),
    "purchase-order": (PurchaseOrder, PurchaseOrderEditForm, "accounts.manage_purchase_order", update_purchase_order, delete_purchase_order, "po-list"),
}


@login_required
@require_http_methods(["GET", "POST"])
def record_edit(request, kind, pk):
    if kind not in MAINTENANCE:
        raise Http404
    model, form_class, permission, update, _, list_url = MAINTENANCE[kind]
    require_permission(request.user, permission)
    record = get_object_or_404(model, pk=pk)
    if record.is_used:
        return render(request, "contracts/record_locked.html", {"record": record}, status=409)
    form = form_class(request.POST if request.method == "POST" else None, request.FILES or None, instance=record)
    if request.method == "POST" and form.is_valid():
        try:
            update(actor=request.user, pk=pk, **form.cleaned_data)
        except ValidationError as error:
            add_service_errors(form, error)
        except IntegrityError:
            form.add_error(None, "The record conflicts with existing data. Refresh and try again.")
        else:
            messages.success(request, "Changes saved.")
            return redirect(list_url)
    return render(request, "record_form.html", {"form": form, "title": f"Edit {record}", "delete_kind": kind, "record": record})


@login_required
@require_http_methods(["GET", "POST"])
def record_delete(request, kind, pk):
    if kind not in MAINTENANCE:
        raise Http404
    model, _, permission, _, delete, list_url = MAINTENANCE[kind]
    require_permission(request.user, permission)
    record = get_object_or_404(model, pk=pk)
    if record.is_used:
        return render(request, "contracts/record_locked.html", {"record": record}, status=409)
    error = None
    if request.method == "POST":
        try:
            delete(actor=request.user, pk=pk)
        except ValidationError as failure:
            error = " ".join(failure.messages)
        except IntegrityError:
            error = "The record is now referenced by another record and cannot be deleted."
        else:
            messages.success(request, "Record deleted. Its audit history has been retained.")
            return redirect(list_url)
    return render(request, "contracts/confirm_delete.html", {"record": record, "error": error, "list_url": list_url})
