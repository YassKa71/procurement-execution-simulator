from django import forms
from .models import Contract, InternalPolicy, PurchaseOrder


class ContractForm(forms.ModelForm):
    class Meta:
        model = Contract
        fields = ["reference", "supplier", "title", "start_date", "end_date", "renewable", "document"]
        widgets = {"start_date": forms.DateInput(attrs={"type": "date"}), "end_date": forms.DateInput(attrs={"type": "date"})}


class PurchaseOrderEditForm(forms.ModelForm):

    class Meta:
        model = PurchaseOrder
        fields = ["reference", "supplier", "contract", "order_date", "requested_delivery", "description", "document"]
        widgets = {"order_date": forms.DateInput(attrs={"type": "date"}), "requested_delivery": forms.DateInput(attrs={"type": "date"})}


class PurchaseOrderForm(PurchaseOrderEditForm):
    create_agreement = forms.TypedChoiceField(
        label="Create an associated Exchange Agreement?",
        choices=[("no", "No"), ("yes", "Yes")],
        coerce=lambda value: value == "yes", empty_value=False,
        required=False, initial="no", widget=forms.RadioSelect,
    )
    internal_policy = forms.ModelChoiceField(
        queryset=InternalPolicy.objects.all(), required=False,
        help_text="Applies only if you choose to create an agreement now.",
    )

    def clean(self):
        data = super().clean()
        if data.get("internal_policy") and not data.get("create_agreement"):
            self.add_error("internal_policy", "Select a policy only when creating an associated agreement.")
        return data


class InternalPolicyForm(forms.ModelForm):
    class Meta:
        model = InternalPolicy
        fields = ["reference", "version", "title", "document"]
