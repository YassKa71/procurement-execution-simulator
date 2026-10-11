from django import forms
from .models import ExchangeAgreement
from contracts.models import PurchaseOrder


class ExchangeAgreementForm(forms.ModelForm):
    class Meta:
        model = ExchangeAgreement
        fields = ["supplier", "contract", "purchase_order", "internal_policy", "start_date", "deadline"]
        widgets = {"start_date": forms.DateInput(attrs={"type": "date"}), "deadline": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["purchase_order"].queryset = PurchaseOrder.objects.filter(exchange_agreements__isnull=True)
        self.fields["purchase_order"].help_text = "Only purchase orders without an existing agreement are available."

    def clean(self):
        data = super().clean()
        po = data.get("purchase_order")
        if po and po.contract_id and not data.get("contract"):
            data["contract"] = po.contract
        return data
