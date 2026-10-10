"""Security headers (index.php) + maintenance-mode gate (.maintenance file)."""
from pathlib import Path

from django.conf import settings
from django.shortcuts import render
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
