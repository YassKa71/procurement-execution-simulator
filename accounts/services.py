from django.core.exceptions import PermissionDenied

def require_permission(actor, permission):
    """Shared entry guard for business services; never infer permissions from names."""
    if not actor.is_authenticated or not actor.is_active or not actor.has_perm(permission):
        raise PermissionDenied("You do not have permission to perform this action.")
