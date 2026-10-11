from django.contrib import admin
from django.shortcuts import redirect
from .models import Contract, InternalPolicy, PurchaseOrder


class ReadOnlyRecordAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class MaintainedRecordAdmin(admin.ModelAdmin):
    """Admin maintenance reuses business forms, services, locks and audit events."""
    actions = None
    manage_permission = ""
    create_permission = ""
    kind = ""
    create_url = ""

    def has_add_permission(self, request):
        return request.user.has_perm(self.create_permission)

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm(self.manage_permission) and (obj is None or not obj.is_used)

    def has_delete_permission(self, request, obj=None):
        return self.has_change_permission(request, obj)

    def add_view(self, request, form_url="", extra_context=None):
        return redirect(self.create_url)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        obj = self.get_object(request, object_id)
        if obj is not None and not obj.is_used:
            return redirect("record-edit", kind=self.kind, pk=obj.pk)
        return super().change_view(request, object_id, form_url, extra_context)

    def delete_view(self, request, object_id, extra_context=None):
        return redirect("record-delete", kind=self.kind, pk=object_id)


@admin.register(Contract)
class ContractAdmin(MaintainedRecordAdmin):
    list_display = ("reference", "supplier", "title", "start_date", "end_date")
    manage_permission = "accounts.manage_contract"
    create_permission = "accounts.create_contract"
    kind = "contract"
    create_url = "contract-create"


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(MaintainedRecordAdmin):
    list_display = ("reference", "supplier", "contract", "order_date")
    manage_permission = "accounts.manage_purchase_order"
    create_permission = "accounts.create_purchase_order"
    kind = "purchase-order"
    create_url = "po-create"


@admin.register(InternalPolicy)
class InternalPolicyAdmin(MaintainedRecordAdmin):
    list_display = ("reference", "version", "title")
    manage_permission = "accounts.manage_policy"
    create_permission = "accounts.manage_policy"
    kind = "policy"
    create_url = "policy-create"
