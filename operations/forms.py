from django import forms
from django.utils import timezone
from .models import GoodsReceipt, Requirement, ValidationRequest, CaseDocument, CaseEmail, SupplierInvoice


class GoodsReceiptForm(forms.ModelForm):
    class Meta:
        model = GoodsReceipt
        fields = ["description", "quantity", "unit", "reception_date", "comment", "document"]
        widgets = {"reception_date": forms.DateInput(attrs={"type": "date"})}


class GoodsAssessmentForm(forms.Form):
    valid_goods_percentage = forms.DecimalField(min_value=0, max_value=100, max_digits=5, decimal_places=2)
    assessment_note = forms.CharField(required=False, widget=forms.Textarea, help_text="Required when correcting a previous assessment.")


class RequirementForm(forms.ModelForm):
    class Meta:
        model = Requirement
        fields = ["note", "deadline"]
        widgets = {"deadline": forms.DateInput(attrs={"type": "date"})}


class ValidationRequestForm(forms.Form):
    person = forms.CharField(max_length=200)
    email = forms.EmailField()
    question = forms.CharField(widget=forms.Textarea)
    note = forms.CharField(required=False, widget=forms.Textarea, help_text="Requirement context. Defaults to the question if left empty.")
    request_date = forms.DateField(initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}))
    deadline = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    send_email = forms.BooleanField(required=False, initial=False, label="Send this request by email")


class ValidationResponseForm(forms.Form):
    validation = forms.ModelChoiceField(queryset=ValidationRequest.objects.none(), label="Outstanding validation request")
    decision = forms.ChoiceField(choices=[("APPROVED", "Approved"), ("REJECTED", "Rejected")])
    response_date = forms.DateField(initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}))
    responder = forms.CharField(max_length=200)
    response_comment = forms.CharField(required=False, widget=forms.Textarea)
    document = forms.FileField(required=False, label="Supporting document")

    def __init__(self, *args, agreement, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["validation"].queryset = ValidationRequest.objects.filter(
            requirement__exchange_agreement=agreement, requirement__status="OPEN", status="PENDING"
        ).select_related("requirement")


class CaseDocumentForm(forms.ModelForm):
    class Meta:
        model = CaseDocument
        fields = ["title", "category", "date", "notes", "document"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}


class CaseEmailForm(forms.ModelForm):
    class Meta:
        model = CaseEmail
        fields = ["sender", "recipients", "subject", "direction", "occurred_at", "body", "document"]
        widgets = {"occurred_at": forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"})}


class SupplierInvoiceForm(forms.ModelForm):
    class Meta:
        model = SupplierInvoice
        fields = ["reference", "amount", "currency", "invoice_date", "due_date", "document", "notes"]
        widgets = {"invoice_date": forms.DateInput(attrs={"type": "date"}), "due_date": forms.DateInput(attrs={"type": "date"})}

    def clean_currency(self):
        return self.cleaned_data["currency"].upper()


class ConfirmationForm(forms.Form):
    confirm = forms.BooleanField(label="I confirm this action")
