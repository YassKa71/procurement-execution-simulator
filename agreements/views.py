from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_safe, require_http_methods
from accounts.forms import add_service_errors
from accounts.services import require_permission
from suppliers.models import Supplier
from .forms import ExchangeAgreementForm
from .models import ExchangeAgreement
from .services import create_exchange_agreement
from operations.models import Requirement, ValidationRequest


@login_required
@require_safe
def agreement_list(request):
    require_permission(request.user, "accounts.access_workspace")
    records = ExchangeAgreement.objects.select_related("supplier", "contract", "purchase_order")
    query = request.GET.get("q", "").strip()
    if query:
        records = records.filter(Q(reference__icontains=query) | Q(supplier__name__icontains=query))
    status = request.GET.get("status", "")
    if status in ExchangeAgreement.Status.values:
        records = records.filter(status=status)
    for field in ("supplier", "contract"):
        value = request.GET.get(field, "")
        if value.isascii() and value.isdecimal() and len(value) < 19:
            records = records.filter(**{field + "_id": int(value)})
    return render(request, "agreements/list.html", {"page_obj": Paginator(records, 25).get_page(request.GET.get("page")), "q": query, "suppliers": Supplier.objects.all()})


@login_required
@require_http_methods(["GET", "POST"])
def agreement_create(request):
    require_permission(request.user, "accounts.create_exchange_agreement")
    form = ExchangeAgreementForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            agreement = create_exchange_agreement(actor=request.user, **form.cleaned_data)
        except ValidationError as error:
            add_service_errors(form, error)
        except IntegrityError:
            form.add_error(None, "The agreement could not be saved. Check its commercial basis and try again.")
        else:
            messages.success(request, "Exchange agreement created.")
            return redirect("agreement-detail", pk=agreement.pk)
    return render(request, "record_form.html", {"form": form, "title": "Create exchange agreement", "explanation": "Select a Contract, a Purchase Order, or both. At least one is required."})


@login_required
@require_safe
def agreement_detail(request, pk):
    require_permission(request.user, "accounts.access_workspace")
    agreement = get_object_or_404(ExchangeAgreement.objects.select_related("supplier", "contract", "purchase_order", "internal_policy", "responsible_user"), pk=pk)
    requirements = list(Requirement.objects.filter(exchange_agreement=agreement).select_related("validation_request"))
    open_requirements = [item for item in requirements if item.status == "OPEN"]
    resolved_requirements = [item for item in requirements if item.status != "OPEN"]
    history = Paginator(agreement.events.all(), 30)
    history_page = history.get_page(request.GET.get("history_page", history.num_pages))
    active_tab = request.GET.get("tab", "history" if "history_page" in request.GET else "overview")
    if active_tab not in {"overview", "requirements", "goods", "history"}:
        active_tab = "overview"
    context = {
        "active_tab": active_tab,
        "recent_events": agreement.events.order_by("-timestamp", "-pk")[:4],
        "unassessed_receipts": agreement.goodsreceipts.filter(is_validated=False).count(),
        "agreement": agreement,
        "can_operate": agreement.status == "OPEN" and request.user.has_perm("accounts.operate_exchange_agreement"),
        "open_requirements": open_requirements, "resolved_requirements": resolved_requirements,
        "pending_validations": ValidationRequest.objects.filter(requirement__exchange_agreement=agreement, status="PENDING").count(),
        "receipts": agreement.goodsreceipts.select_related("assessed_by"),
        "documents": agreement.casedocuments.all(), "emails": agreement.caseemails.all(),
        "supplier_invoices": agreement.supplierinvoices.all(), "history_page": history_page,
    }
    return render(request, "agreements/detail.html", context)
