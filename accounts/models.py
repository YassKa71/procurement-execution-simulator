from django.contrib.auth.models import AbstractUser

class User(AbstractUser):
    class Meta(AbstractUser.Meta):
        permissions = [
            ("access_workspace", "Can access the business workspace"),
            ("create_contract", "Can create a contract"),
            ("create_exchange_agreement", "Can create an agreement directly"),
            ("create_purchase_order", "Can create a PO and its agreement"),
            ("operate_exchange_agreement", "Can operate an agreement"),
            ("manage_digital_invoice", "Can create and modify eligible digital invoices"),
            ("mark_invoice_ready", "Can mark digital invoices ready to pay"),
            ("execute_payment", "Can execute simulated payments"),
        ]
