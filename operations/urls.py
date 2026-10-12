from django.urls import path
from . import views

urlpatterns = [
    path("actions/<slug:action>/", views.create_action, name="case-action"),
    path("receipts/<int:receipt_id>/assess/", views.assess_receipt, name="assess-receipt"),
    path("requirements/<int:requirement_id>/<slug:action>/", views.requirement_action, name="requirement-action"),
    path("validations/<int:validation_id>/<slug:action>/", views.validation_action, name="validation-action"),
    path("resources/<slug:kind>/<int:resource_id>/download/", views.download, name="case-download"),
]
