"""Public auth views: login / forgot / 2fa / 404 / install-guard.

Mirrors pp-content/pp-admin/{login,forgot,2fa}.php + pp-adapter.php login,
2fa-verify and forgot-password handlers (BCRYPT, email-or-username, permission
row required, temp_password fallback, 2fa_secret auto-create, reset_limit,
forgot.password hook, browser_log). Wire format is HTML POST (PHP used AJAX
JSON); the enforced rules are identical.
"""
import json

from django.contrib import messages
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.shortcuts import redirect, render

from core import hooks
from core.models import Admin, Brand, BrowserLog, Permission
from core.money import generate_strong_password, get_user_device_info
from core.totp import generate_secret, verify_totp


def _is_email(value: str) -> bool:
    try:
        validate_email(value)
        return True
    except ValidationError:
        return False


def _remember_admin(request, admin: Admin, cookie: str | None = None):
    # Mirror PHP: bin2hex(random_bytes(16)) cookie stored server-side in
    # browser_log AND client-side; companion pairing keys off this cookie.
    # 2FA success reuses the pending cookie (PHP promotes pp_2fa -> pp_admin).
    import secrets as _secrets
    request.session.cycle_key()
    request.session["pp_admin_id"] = admin.a_id
    request.session["pp_admin_role"] = admin.role
    cookie = cookie or _secrets.token_hex(16)
    request.session["pp_cookie"] = cookie
    perms = Permission.objects.filter(a_id=admin.a_id, status="active").order_by("id")
    first = perms.first()
    if first:
        request.session["pp_brand_id"] = first.brand_id
    info = get_user_device_info(request)
    BrowserLog.objects.create(a_id=admin.a_id, cookie=cookie[:40], browser=info["browser"][:10],
                              device=info["device"][:10], ip=(info["ip_address"] or "")[:15])
    return cookie


def admin_login(request):
    if request.session.get("pp_admin_id"):
        return redirect("/admin/")
    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        if not username or not password:
            messages.error(request, "Please fill in all required fields before proceeding.")
            return render(request, "core/login.html")
        lookup = {"email": username} if _is_email(username) else {"username": username}
        admin = Admin.objects.filter(**lookup).first()
        ok = admin is not None and (
            check_password(password, admin.password)
            or (admin.temp_password and check_password(password, admin.temp_password)))
        if not ok:
            messages.error(request, "The email or password you entered is incorrect.")
            return render(request, "core/login.html")
        if admin.status != "active":
            messages.error(request, "Your account has been suspended. Please contact with your admin.")
            return render(request, "core/login.html")
        perm = Permission.objects.filter(a_id=admin.a_id, status="active").order_by("id").first()
        if not perm:
            messages.error(request, "You don't have permission to manage brands. Contact your admin.")
            return render(request, "core/login.html")
        if not Brand.objects.filter(brand_id=perm.brand_id).exists():
            messages.error(request, "You don't have permission to manage brands. Contact your admin.")
            return render(request, "core/login.html")
        if not admin.twofa_secret or admin.twofa_secret == "--":
            admin.twofa_secret = generate_secret()
            admin.save(update_fields=["twofa_secret", "updated_date"])
        if admin.twofa_status == "enable":
            request.session["pp_2fa_pending"] = admin.a_id
            import secrets as _secrets2
            pending_cookie = _secrets2.token_hex(16)
            request.session["pp_cookie"] = pending_cookie
            resp = redirect("/2fa/")
            resp.set_cookie("pp_2fa", pending_cookie,
                            max_age=365 * 24 * 3600, httponly=True, samesite="Lax")
            return resp
        cookie = _remember_admin(request, admin)
        resp = redirect("/admin/")
        resp.set_cookie("pp_admin", cookie, max_age=365 * 24 * 3600, httponly=True, samesite="Lax")
        if request.session.get("pp_brand_id"):
            resp.set_cookie("pp_brand", request.session["pp_brand_id"],
                            max_age=365 * 24 * 3600, httponly=True, samesite="Lax")
        return resp
    return render(request, "core/login.html")


def admin_forgot(request):
    if request.method == "POST":
        email = (request.POST.get("email") or request.POST.get("email-address") or "").strip()
        if not _is_email(email):
            messages.error(request, "Please enter a valid email address.")
            return render(request, "core/forgot.html")
        admin = Admin.objects.filter(email=email, status="active").first()
        if admin:
            try:
                limit = int(str(admin.reset_limit).strip())
            except (TypeError, ValueError):
                limit = 0
            if limit > 0:
                new_pw = generate_strong_password(8)
                admin.temp_password = make_password(new_pw)
                admin.reset_limit = str(limit - 1)
                admin.save(update_fields=["temp_password", "reset_limit", "updated_date"])
                hooks.do_action("forgot.password", {"full_name": admin.full_name,
                                                    "new_password": new_pw, "email": admin.email})
            else:
                messages.error(request, "You have reached the maximum number of reset attempts.")
                return render(request, "core/forgot.html")
        messages.info(request, "If your account doesn't exist, you will not receive the email.")
        return redirect("/login/")
    return render(request, "core/forgot.html")


def admin_2fa(request):
    pending = request.session.get("pp_2fa_pending")
    if not pending:
        return redirect("/login/")
    admin = Admin.objects.filter(a_id=pending).first()
    if not admin or admin.twofa_status != "enable":
        return redirect("/login/")
    if request.method == "POST":
        # PHP posts six 1-char boxes code_one..six; accept that or a single code.
        parts = [request.POST.get(f"code_{n}", "") for n in ("one", "two", "three", "four", "five", "six")]
        if any(parts) and any(not part for part in parts):
            messages.error(request, "Please fill in all required fields before proceeding.")
            return render(request, "core/2fa.html")
        code = "".join(parts) if any(parts) else (request.POST.get("code") or "")
        if verify_totp(admin.twofa_secret, code, window=2):
            pending_cookie = request.session.get("pp_cookie", "")
            del request.session["pp_2fa_pending"]
            cookie = _remember_admin(request, admin, cookie=pending_cookie or None)
            resp = redirect("/admin/")
            resp.set_cookie("pp_admin", cookie, max_age=365 * 24 * 3600, httponly=True,
                            samesite="Lax")
            resp.delete_cookie("pp_2fa")
            if request.session.get("pp_brand_id"):
                resp.set_cookie("pp_brand", request.session["pp_brand_id"],
                                max_age=365 * 24 * 3600, httponly=True, samesite="Lax")
            return resp
        messages.error(request, "The code you entered is incorrect. Please try again.")
    return render(request, "core/2fa.html")


def page_404(request, exception=None):
    return render(request, "core/404.html", status=404)


def homepage_redirect(request):
    """Mirror case 'homepageRedirect' (+ traversal rewrite target)."""
    from django.conf import settings

    target = (settings.PIPRAPAY_PATHS.get("homepage_redirect") or "").strip()
    if not target:
        return redirect("/login/")
    if "://" in target:
        return redirect(target)
    return redirect("https://" + target)


def set_brand(request):
    """Mirror set-default-brand: POST-only, permission row + brands-table check."""
    from staff.auth import login_required

    @login_required
    def _inner(request):
        if request.method != "POST":
            messages.error(request, "Invalid request.")
            return redirect("/admin/dashboard/")
        brand_id = (request.POST.get("brand_id") or "").strip()
        if not brand_id:
            messages.error(request, "Please fill in all required fields before proceeding.")
            return redirect("/admin/dashboard/")
        if not Permission.objects.filter(a_id=request.pp_admin.a_id, brand_id=brand_id,
                                         status="active").exists():
            messages.error(request, "You don't have permission to manage this brand.")
            return redirect("/admin/dashboard/")
        if not Brand.objects.filter(brand_id=brand_id).exists():
            messages.error(request, "You don't have permission to manage this brand.")
            return redirect("/admin/dashboard/")
        request.session["pp_brand_id"] = brand_id
        resp = redirect("/admin/dashboard/")
        resp.set_cookie("pp_brand", brand_id, max_age=365 * 24 * 3600, httponly=True,
                        samesite="Lax")
        return resp
    return _inner(request)
