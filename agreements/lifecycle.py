from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, render
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_http_methods
from accounts.forms import add_service_errors
from accounts.services import require_permission
from operations.views import _action_success
from .models import ExchangeAgreement
from .services import close_exchange_agreement, reopen_exchange_agreement


class CloseForm(forms.Form):
    reason = forms.ChoiceField(choices=ExchangeAgreement.ClosureReason.choices)
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Explanation (optional)")
    confirm = forms.BooleanField(label="I understand that closing cancels all outstanding requirements and pending validations.")


class ReopenForm(forms.Form):
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Explanation (optional)")
    confirm = forms.BooleanField(label="Reopen this agreement. Previously cancelled requirements will remain cancelled.")


@login_required
@require_http_methods(["GET", "POST"])
@xframe_options_sameorigin
def lifecycle_action(request, pk, action):
    require_permission(request.user, "accounts.operate_exchange_agreement")
    agreement = get_object_or_404(ExchangeAgreement, pk=pk)
    closing = action == "close"
    form = (CloseForm if closing else ReopenForm)(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        payload = dict(form.cleaned_data)
        payload.pop("confirm")
        try:
            (close_exchange_agreement if closing else reopen_exchange_agreement)(actor=request.user, agreement_id=pk, **payload)
        except ValidationError as error:
            add_service_errors(form, error)
        else:
            messages.success(request, "Agreement closed." if closing else "Agreement reopened.")
            return _action_success(request, pk)
    return render(request, "agreements/lifecycle_form.html", {"agreement": agreement, "form": form, "closing": closing,
        "requirements": agreement.requirements.filter(status="OPEN").select_related("validation_request"),
        "blockers": agreement.digital_invoices.filter(status="READY_TO_PAY"),
        "title": "Close agreement" if closing else "Reopen agreement",
        "base_template": "operations/panel_base.html" if request.GET.get("panel") == "1" else "base.html"})
