from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from .services import require_permission

@login_required
def dashboard(request):
    require_permission(request.user, "accounts.access_workspace")
    return render(request, "dashboard.html")
