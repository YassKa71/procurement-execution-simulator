from django import forms
from .models import DigitalInvoice


class DigitalInvoiceForm(forms.ModelForm):
    expected_revision = forms.IntegerField(min_value=1, required=False, widget=forms.HiddenInput)

    class Meta:
        model = DigitalInvoice
        fields = ("description", "calculation", "amount", "currency", "due_date")
        widgets = {"description": forms.Textarea(attrs={"rows": 3}), "calculation": forms.Textarea(attrs={"rows": 4}), "due_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["expected_revision"].required = True
            self.initial["expected_revision"] = self.instance.revision


class InvoiceConfirmationForm(forms.Form):
    expected_revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    confirm = forms.BooleanField(label="I confirm this action for the invoice shown above.")
