"""Root URLconf. Mirrors index.php router (?page= switch)."""
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path
from django.http import HttpResponse

from core import views as core_views
from core import install_views as core_install
from django.http import JsonResponse as _JsonResponse


def ipn_empty(request):
    return _JsonResponse({"error": {"code": "INVALID_GATEWAY",
                                    "message": "The Gateway provided is incorrect or invalid."}},
                         status=400)

urlpatterns = [
    # Public: login / forgot / 2fa  (index.php case 'login','forgot','2fa')
    path("login/", core_views.admin_login, name="login"),
    path("forgot/", core_views.admin_forgot, name="forgot"),
    path("2fa/", core_views.admin_2fa, name="2fa"),
    path("404/", core_views.page_404, name="page-404"),
    path("set-brand/", core_views.set_brand, name="set-brand"),
    path("install/", core_install.install_index, name="install"),
    path("install/requirements/", core_install.install_requirements, name="install-reqs"),
    path("install/admin/", core_install.install_admin, name="install-admin"),

    # IPN per gateway  (index.php case 'ipn' -> ipn/<gateway_id>)
    path("ipn/<str:gateway_id>/", include("gateways.ipn_urls")),
    path("ipn/", ipn_empty, name="gateway-ipn-empty"),
    path("ipn", ipn_empty),

    # REST API  (index.php case 'api' -> api/checkout/redirect|popup, verify, refund)
    path("api/", include("api.urls")),

    # Checkout surfaces (index.php case $path_payment/$path_invoice/$path_payment_link)
    path("", include("checkout.urls")),

    # Staff admin panel (index.php case $path_admin)
    path("admin/", include("staff.urls")),

    # Cron (index.php case $path_cron -> cron/<token>)
    path("cron/", include("cron_app.urls")),

    # Maintenance / requirement helpers
    path("health/", lambda r: HttpResponse("ok"), name="health"),

    # homepageRedirect (explicit ?page=homepageRedirect + traversal rewrite target)
    path("", core_views.homepage_redirect, name="home"),
]

handler404 = "core.views.page_404"

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
