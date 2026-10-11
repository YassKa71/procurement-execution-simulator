from django.contrib import admin
from contracts.admin import ReadOnlyRecordAdmin
from .models import ExchangeAgreement


@admin.register(ExchangeAgreement)
class ExchangeAgreementAdmin(ReadOnlyRecordAdmin):
    list_display = ("reference", "supplier", "status", "created_at")
