"""Session auth + permission decorators mirroring pp-admin/index.php guards."""
from functools import wraps
import json

from django.shortcuts import redirect, render

from core.models import Admin, Permission
from core.permissions import can_access_page, has_permission


def current_admin(request):
    a_id = request.session.get("pp_admin_id")
    if not a_id:
        return None
    return Admin.objects.filter(a_id=a_id, status="active").first()


def admin_permissions(admin: Admin) -> dict:
    if not admin or admin.role == "admin":
        return {"resources": {}, "pages": {}}
    row = Permission.objects.filter(a_id=admin.a_id, status="active").first()
    if not row:
        return {"resources": {}, "pages": {}}
    try:
        return json.loads(row.permission or "{}")
    except ValueError:
        return {"resources": {}, "pages": {}}


def login_required(view):
    @wraps(view)
    def inner(request, *args, **kwargs):
        admin = current_admin(request)
        if not admin:
            return redirect("/login/")
        request.pp_admin = admin
        request.pp_permissions = admin_permissions(admin)
        return view(request, *args, **kwargs)
    return inner


def page_required(page: str):
    def deco(view):
        @wraps(view)
        def inner(request, *args, **kwargs):
            if not can_access_page(request.pp_permissions, page, request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            return view(request, *args, **kwargs)
        return inner
    return deco


def perm_required(module: str, action: str = "view"):
    def deco(view):
        @wraps(view)
        def inner(request, *args, **kwargs):
            if not has_permission(request.pp_permissions, module, action, request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            return view(request, *args, **kwargs)
        return inner
    return deco
