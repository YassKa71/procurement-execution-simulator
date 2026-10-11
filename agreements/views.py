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
    return render(request, "agreements/detail.html", {"agreement": agreement})
