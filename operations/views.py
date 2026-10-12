from pathlib import PurePosixPath
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_safe
from django.views.decorators.clickjacking import xframe_options_sameorigin
from accounts.forms import add_service_errors
from accounts.services import require_permission
from agreements.models import ExchangeAgreement
from . import forms, services
from .models import GoodsReceipt, Requirement, ValidationRequest, CaseDocument, CaseEmail, SupplierInvoice


CREATE_ACTIONS = {
    "receive-goods": ("Declare goods received", forms.GoodsReceiptForm, services.declare_goods_received),
    "create-requirement": ("Create requirement", forms.RequirementForm, services.create_requirement),
    "request-validation": ("Request validation", forms.ValidationRequestForm, services.request_validation),
    "record-validation": ("Record validation response", forms.ValidationResponseForm, services.record_validation),
    "attach-document": ("Attach document", forms.CaseDocumentForm, services.attach_document),
    "attach-email": ("Attach email", forms.CaseEmailForm, services.attach_email),
    "attach-invoice": ("Attach supplier invoice", forms.SupplierInvoiceForm, services.attach_supplier_invoice),
}


def _operable_agreement(request, pk):
    require_permission(request.user, "accounts.operate_exchange_agreement")
    return get_object_or_404(ExchangeAgreement, pk=pk)


def _display_form(request, agreement, form, title, *, explanation="", subject=None):
    if agreement.status != "OPEN":
        return render(request, "operations/closed.html", {"agreement": agreement}, status=409)
    return render(request, "operations/action_form.html", {
        "agreement": agreement, "form": form, "title": title, "explanation": explanation, "subject": subject,
        "base_template": "operations/panel_base.html" if request.GET.get("panel") == "1" else "base.html",
    })


def _action_success(request, pk):
    if request.GET.get("panel") == "1":
        return render(request, "operations/panel_success.html")
    return redirect("agreement-detail", pk=pk)


def _submit(request, agreement, form, service, **parameters):
    if request.method != "POST" or not form.is_valid():
        return False
    data = dict(form.cleaned_data)
    data.pop("confirm", None)
    if "validation" in data:
        data["validation_id"] = data.pop("validation").pk
    try:
        service(actor=request.user, agreement_id=agreement.pk, **parameters, **data)
    except ValidationError as error:
        add_service_errors(form, error)
    except IntegrityError:
        form.add_error(None, "The action could not be saved because the record changed. Refresh the case and try again.")
    else:
        messages.success(request, "Action recorded in the case history.")
        return True
    return False


@login_required
@require_http_methods(["GET", "POST"])
@xframe_options_sameorigin
def create_action(request, pk, action):
    if action not in CREATE_ACTIONS:
        raise Http404
    agreement = _operable_agreement(request, pk)
    title, form_class, service = CREATE_ACTIONS[action]
    kwargs = {}
    if action == "record-validation":
        kwargs["agreement"] = agreement
        selected = request.GET.get("validation", "")
        if selected.isascii() and selected.isdecimal() and len(selected) < 19:
            kwargs["initial"] = {"validation": selected}
    form = form_class(request.POST if request.method == "POST" else None, request.FILES or None, **kwargs)
    if _submit(request, agreement, form, service):
        return _action_success(request, pk)
    return _display_form(request, agreement, form, title)


@login_required
@require_http_methods(["GET", "POST"])
@xframe_options_sameorigin
def assess_receipt(request, pk, receipt_id):
    agreement = _operable_agreement(request, pk)
    receipt = get_object_or_404(GoodsReceipt, pk=receipt_id, exchange_agreement=agreement)
    form = forms.GoodsAssessmentForm(request.POST if request.method == "POST" else None,
                                     initial={"valid_goods_percentage": receipt.valid_goods_percentage})
    if _submit(request, agreement, form, services.assess_goods_receipt, receipt_id=receipt.pk):
        return _action_success(request, pk)
    return _display_form(request, agreement, form, "Correct goods assessment" if receipt.is_validated else "Assess goods",
                         explanation="Recording an assessment does not automatically approve a validation request or payment.", subject=receipt)


@login_required
@require_http_methods(["GET", "POST"])
@xframe_options_sameorigin
def requirement_action(request, pk, requirement_id, action):
    agreement = _operable_agreement(request, pk)
    requirement = get_object_or_404(Requirement, pk=requirement_id, exchange_agreement=agreement)
    if action == "edit":
        form = forms.RequirementForm(request.POST if request.method == "POST" else None, instance=requirement)
        service, title = services.update_requirement, "Update requirement"
    elif action in ("complete", "cancel"):
        form = forms.ConfirmationForm(request.POST if request.method == "POST" else None)
        service = services.complete_requirement if action == "complete" else services.cancel_requirement
        title = "Complete requirement" if action == "complete" else "Cancel requirement"
    else:
        raise Http404
    if _submit(request, agreement, form, service, requirement_id=requirement.pk):
        return _action_success(request, pk)
    return _display_form(request, agreement, form, title, subject=requirement)


@login_required
@require_http_methods(["GET", "POST"])
@xframe_options_sameorigin
def validation_action(request, pk, validation_id, action):
    agreement = _operable_agreement(request, pk)
    validation = get_object_or_404(ValidationRequest, pk=validation_id, requirement__exchange_agreement=agreement)
    if action not in ("cancel", "send"):
        raise Http404
    form = forms.ConfirmationForm(request.POST if request.method == "POST" else None)
    service = services.cancel_validation if action == "cancel" else services.send_validation_request
    title = "Cancel validation request" if action == "cancel" else "Send validation email"
    if _submit(request, agreement, form, service, validation_id=validation.pk):
        return _action_success(request, pk)
    return _display_form(request, agreement, form, title, subject=validation,
                         explanation=f"Recipient: {validation.person} <{validation.email}>. {validation.question}")


DOWNLOAD_MODELS = {"receipt": GoodsReceipt, "document": CaseDocument, "email": CaseEmail, "invoice": SupplierInvoice, "validation": ValidationRequest}


@login_required
@require_safe
def download(request, pk, kind, resource_id):
    require_permission(request.user, "accounts.access_workspace")
    model = DOWNLOAD_MODELS.get(kind)
    if model is None:
        raise Http404
    scope = {"requirement__exchange_agreement_id" if model is ValidationRequest else "exchange_agreement_id": pk}
    resource = get_object_or_404(model, pk=resource_id, **scope)
    if not resource.document:
        raise Http404("No document attached.")
    try:
        stream = resource.document.open("rb")
    except FileNotFoundError:
        raise Http404("Document unavailable.")
    response = FileResponse(stream, as_attachment=True, filename=PurePosixPath(resource.document.name).name,
                            content_type="application/octet-stream")
    response["Cache-Control"] = "private, no-store"
    return response
