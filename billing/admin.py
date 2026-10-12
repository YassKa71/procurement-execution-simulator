from django.contrib import admin
from contracts.admin import ReadOnlyRecordAdmin
from .models import DigitalInvoice, Payment


@admin.register(DigitalInvoice)
class DigitalInvoiceAdmin(ReadOnlyRecordAdmin):
    list_display = ("reference", "exchange_agreement", "amount", "currency", "status", "due_date")
    list_filter = ("status", "currency")


@admin.register(Payment)
class PaymentAdmin(ReadOnlyRecordAdmin):
    list_display = ("reference", "invoice", "amount", "currency", "executed_at", "executed_by")
