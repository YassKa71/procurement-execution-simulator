from django.contrib import admin
from contracts.admin import ReadOnlyRecordAdmin
from .models import GoodsReceipt, Requirement, ValidationRequest, CaseDocument, CaseEmail, SupplierInvoice


for model in (GoodsReceipt, Requirement, ValidationRequest, CaseDocument, CaseEmail, SupplierInvoice):
    admin.site.register(model, ReadOnlyRecordAdmin)
