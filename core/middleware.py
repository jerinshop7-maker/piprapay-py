"""Security headers mirroring index.php header() calls."""
from django.utils.deprecation import MiddlewareMixin


class PipraPaySecurityHeadersMiddleware(MiddlewareMixin):
    def process_response(self, request, response):
        response.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.setdefault("X-Content-Type-Options", "nosniff")
        response.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response
