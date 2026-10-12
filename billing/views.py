from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_http_methods, require_safe
from accounts.forms import add_service_errors
from accounts.services import require_permission
from agreements.models import ExchangeAgreement
from suppliers.models import Supplier
from . import services
from .forms import DigitalInvoiceForm, InvoiceConfirmationForm
from .models import DigitalInvoice


@login_required
@require_safe
def invoice_list(request):
    require_permission(request.user, "accounts.access_workspace")
    records = DigitalInvoice.objects.select_related("exchange_agreement__supplier")
    q = request.GET.get("q", "").strip()
    if q:
        records = records.filter(Q(reference__icontains=q) | Q(description__icontains=q) | Q(exchange_agreement__reference__icontains=q) | Q(exchange_agreement__supplier__name__icontains=q))
    status = request.GET.get("status", "")
    if status in DigitalInvoice.Status.values:
        records = records.filter(status=status)
    for key, field in (("supplier", "exchange_agreement__supplier_id"), ("agreement", "exchange_agreement_id")):
        value = request.GET.get(key, "")
        if value.isascii() and value.isdecimal() and len(value) < 19:
            records = records.filter(**{field: int(value)})
    if request.GET.get("overdue") == "1":
        records = records.filter(status__in=["DRAFT", "READY_TO_PAY"], due_date__lt=timezone.localdate())
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "billing/list.html", {"page_obj": Paginator(records, 25).get_page(request.GET.get("page")), "filter_query": query.urlencode(), "statuses": DigitalInvoice.Status.choices, "suppliers": Supplier.objects.all(), "agreements": ExchangeAgreement.objects.all(), "q": q})


@login_required
@require_http_methods(["GET", "POST"])
@xframe_options_sameorigin
def invoice_action(request, pk, action, invoice_id=None):
    titles = {"create": "Create Digital Invoice", "edit": "Edit Digital Invoice", "ready": "Confirm payment readiness", "draft": "Return invoice to draft", "cancel": "Cancel Digital Invoice", "pay": "Execute simulated payment"}
    if action not in titles or (action == "create") != (invoice_id is None):
        raise Http404
    permission = "execute_payment" if action == "pay" else "mark_invoice_ready" if action == "ready" else "manage_digital_invoice"
    require_permission(request.user, "accounts." + permission)
    agreement = get_object_or_404(ExchangeAgreement, pk=pk)
    invoice = get_object_or_404(DigitalInvoice, pk=invoice_id, exchange_agreement=agreement) if invoice_id else None
    data = request.POST if request.method == "POST" else None
    if action in ("create", "edit"):
        form = DigitalInvoiceForm(data, instance=invoice)
    else:
        form = InvoiceConfirmationForm(data, initial={"expected_revision": invoice.revision})
    explanation = {
        "create": "This internal invoice defines an amount payable. Attach the supplier's invoice separately as evidence.",
        "edit": "Any change returns this invoice to draft and requires a fresh readiness confirmation. The associated agreement cannot be changed.",
        "ready": "Confirm that the amount and the portion of the exchange covered are justified and approved for payment. Other open requirements do not automatically block a partial payment.",
        "draft": "Payment will be unavailable until readiness is confirmed again.",
        "cancel": "Cancellation is final. Create a new draft if another invoice is needed.",
        "pay": "Simulation only: this records a payment and marks the invoice paid. No money is transferred. The paid invoice cannot be edited.",
    }[action]
    available = agreement.status == "OPEN" and (action == "create" or (action == "edit" and invoice.status in ("DRAFT", "READY_TO_PAY")) or (action == "pay" and invoice.status == "READY_TO_PAY") or (action in services.TRANSITIONS and invoice.status in services.TRANSITIONS[action][1]))
    if request.method == "POST" and form.is_valid():
        payload = dict(form.cleaned_data)
        payload.pop("confirm", None)
        try:
            if action == "create":
                payload.pop("expected_revision", None)
                services.create_digital_invoice(actor=request.user, agreement_id=pk, **payload)
            elif action == "edit":
                services.update_digital_invoice(actor=request.user, agreement_id=pk, invoice_id=invoice_id, **payload)
            elif action == "pay":
                services.execute_payment(actor=request.user, agreement_id=pk, invoice_id=invoice_id, **payload)
            else:
                services.transition_invoice(actor=request.user, agreement_id=pk, invoice_id=invoice_id, action=action, **payload)
        except ValidationError as error:
            add_service_errors(form, error)
        except IntegrityError:
            form.add_error(None, "This action conflicts with a newer change. Refresh the invoice and review it again.")
        else:
            messages.success(request, "Invoice action recorded in the case history.")
            if request.GET.get("panel") == "1":
                return render(request, "operations/panel_success.html")
            return redirect(reverse("agreement-detail", args=[pk]) + "?tab=invoices")
    return render(request, "billing/action_form.html", {"agreement": agreement, "invoice": invoice, "form": form, "title": titles[action], "explanation": explanation, "available": available, "base_template": "operations/panel_base.html" if request.GET.get("panel") == "1" else "base.html"})
