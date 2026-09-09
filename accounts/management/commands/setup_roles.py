from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction

COMMON = {"access_workspace", "create_purchase_order", "operate_exchange_agreement", "manage_digital_invoice", "mark_invoice_ready", "execute_payment"}
ROLE_PERMISSIONS = {"Bookkeeper": COMMON, "Manager": COMMON | {"create_contract", "create_exchange_agreement"}}

class Command(BaseCommand):
    help = "Idempotently synchronize the initial business role permissions."

    @transaction.atomic
    def handle(self, *args, **options):
        for name, codenames in ROLE_PERMISSIONS.items():
            group, _ = Group.objects.get_or_create(name=name)
            permissions = Permission.objects.filter(content_type__app_label="accounts", content_type__model="user", codename__in=codenames)
            if permissions.count() != len(codenames):
                raise RuntimeError("Run migrations before setup_roles.")
            group.permissions.set(permissions)
        self.stdout.write(self.style.SUCCESS("Manager and Bookkeeper roles configured."))
