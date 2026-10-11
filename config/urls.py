from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from accounts.views import dashboard
from contracts import views as contracts
from agreements import views as agreements

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
    path("policies/", contracts.policy_list, name="policy-list"),
    path("policies/create/", contracts.policy_create, name="policy-create"),
    path("resources/<str:kind>/<int:pk>/edit/", contracts.record_edit, name="record-edit"),
    path("resources/<str:kind>/<int:pk>/delete/", contracts.record_delete, name="record-delete"),
    path("contracts/", contracts.contract_list, name="contract-list"),
    path("contracts/create/", contracts.contract_create, name="contract-create"),
    path("purchase-orders/", contracts.purchase_order_list, name="po-list"),
    path("purchase-orders/create/", contracts.purchase_order_create, name="po-create"),
    path("purchase-orders/<int:pk>/", contracts.purchase_order_detail, name="po-detail"),
    path("resources/<str:kind>/<int:pk>/download/", contracts.resource_download, name="resource-download"),
    path("exchange-agreements/", agreements.agreement_list, name="agreement-list"),
    path("exchange-agreements/create/", agreements.agreement_create, name="agreement-create"),
    path("exchange-agreements/<int:pk>/", include("operations.urls")),
    path("exchange-agreements/<int:pk>/", agreements.agreement_detail, name="agreement-detail"),
]
