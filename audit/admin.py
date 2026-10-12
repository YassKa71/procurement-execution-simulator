from django.contrib import admin
from contracts.admin import ReadOnlyRecordAdmin
from .models import BusinessEvent


@admin.register(BusinessEvent)
class BusinessEventAdmin(ReadOnlyRecordAdmin):
    list_display = ("timestamp", "event_type", "actor_label", "exchange_agreement")
