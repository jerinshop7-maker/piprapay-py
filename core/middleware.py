"""Security headers (index.php) + maintenance-mode gate (.maintenance file)."""
from pathlib import Path

import re

from django.conf import settings
from django.shortcuts import redirect, render
from django.utils.deprecation import MiddlewareMixin


class PipraPaySecurityHeadersMiddleware(MiddlewareMixin):
    def process_response(self, request, response):
        response.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.setdefault("X-Content-Type-Options", "nosniff")
        response.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response


class PipraPayMaintenanceMiddleware(MiddlewareMixin):
    """Mirror the `.maintenance` router gate: serve pp-maintenance page."""

    def process_request(self, request):
        flag = Path(settings.BASE_DIR) / ".maintenance"
        if flag.exists() and not request.path.startswith("/admin/system-settings/"):
            return render(request, "core/maintenance.html")
        return None


class PipraPayLogoutMiddleware(MiddlewareMixin):
    """Mirror adapter ?logout (pp-adapter.php:248-255): global GET ?logout
    destroys the session and redirects to login."""

    def process_request(self, request):
        if request.method == "GET" and "logout" in request.GET:
            request.session.flush()
            return redirect("/login/")
        return None


class PipraPayLegacyPageMiddleware(MiddlewareMixin):
    """Legacy `?page=` router shim (index.php:86-112): map well-known page
    values to their Django paths so old integrations keep working."""

    def process_request(self, request):
        page = request.GET.get("page", "")
        if not page:
            return None
        page = page.strip().strip("/")
        if ".." in page or not re.match(r"^[a-zA-Z0-9\/_-]+$", page):
            return redirect("/")
        segments = page.split("/")
        route, rest = segments[0], segments[1:]
        mapping = {"login": "/login/", "forgot": "/forgot/", "2fa": "/2fa/",
                   "404": "/404/", "homepageRedirect": "/"}
        if route in mapping:
            return redirect(mapping[route])
        if route in ("ipn", "api", "payment", "invoice", "payment-link", "admin", "cron") and rest:
            return redirect(f"/{route}/" + "/".join(rest) + "/")
        return None


class PipraPayInstallMiddleware(MiddlewareMixin):
    """Request-time install gate (index.php:121): with no admin account yet,
    browser routes go to the installer. API POSTs pass through untouched."""

    def process_request(self, request):
        path = request.path
        if path.startswith(("/install/", "/static/", "/media/", "/health/")):
            return None
        try:
            from core.models import Admin
            if not Admin.objects.exists():
                return redirect("/install/")
        except Exception:
            return None
        return None
