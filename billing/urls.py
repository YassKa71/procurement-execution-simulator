from django.urls import path
from . import views

urlpatterns = [
    path("digital-invoices/", views.invoice_list, name="digital-invoice-list"),
    path("exchange-agreements/<int:pk>/invoices/create/", views.invoice_action, {"action": "create"}, name="digital-invoice-create"),
    path("exchange-agreements/<int:pk>/invoices/<int:invoice_id>/<str:action>/", views.invoice_action, name="digital-invoice-action"),
]
