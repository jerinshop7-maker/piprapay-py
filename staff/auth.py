"""Session auth + permission decorators mirroring pp-admin/index.php guards."""
from functools import wraps
import json

from django.shortcuts import redirect, render

from core.models import Admin, BrowserLog, Permission
from core.permissions import can_access_page, has_permission


def current_admin(request):
    a_id = request.session.get("pp_admin_id")
    if not a_id:
        return None
    admin = Admin.objects.filter(a_id=a_id, status="active").first()
    if not admin:
        return None
    # The pp_cookie browser session must still be live (PHP browser_log gate).
    cookie = request.session.get("pp_cookie", "")
    if not cookie or not BrowserLog.objects.filter(a_id=a_id, cookie=cookie[:40],
                                                   status="active").exists():
        return None
    return admin


def admin_permissions(admin: Admin, brand_id: str = "") -> dict:
    if not admin or admin.role == "admin":
        return {"resources": {}, "pages": {}}
    # PHP resolves the permission row by (a_id, brand_id); fall back to first.
    rows = Permission.objects.filter(a_id=admin.a_id, status="active").order_by("id")
    row = rows.filter(brand_id=brand_id).first() if brand_id else None
    row = row or rows.first()
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
        request.pp_permissions = admin_permissions(admin, request.session.get("pp_brand_id", ""))
        # Current brand scope (pp_brand session, else first permission brand).
        brand_id = request.session.get("pp_brand_id", "")
        if not brand_id:
            first = Permission.objects.filter(a_id=admin.a_id, status="active").order_by("id").first()
            brand_id = first.brand_id if first else ""
            if brand_id:
                request.session["pp_brand_id"] = brand_id
        request.pp_brand_id = brand_id
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
