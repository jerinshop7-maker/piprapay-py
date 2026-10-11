"""UI regression tests: full-UI render sweep, payer CSRF flow, static assets.

Covers the complete-UI pass: Tabler admin shell on every staff page, the
brand-setting hub, gateway verify CSRF tokens (real browsers enforce CSRF;
Django's default test client does not), and WhiteNoise static serving.
"""
import json

from django.contrib.auth.hashers import make_password
from django.contrib.staticfiles import finders
from django.test import Client, TestCase

from core.models import Admin, Brand, Currency, Gateway, Permission, Transaction


def _seed_admin(client, username="root", role="admin", a_id="a1", permission="{}"):
    Brand.objects.create(brand_id="b1", identify_name="B", name="B")
    Currency.objects.create(brand_id="b1", code="BDT", symbol="t", rate=1)
    Admin.objects.create(a_id=a_id, full_name="R", username=username,
                         email=f"{username}@x.co", password=make_password("pw"),
                         role=role)
    Permission.objects.create(brand_id="b1", a_id=a_id, permission=permission)
    client.post("/login/", {"username": username, "password": "pw"})


ADMIN_PAGES = [
    "/admin/dashboard/", "/admin/activities/", "/admin/customers/",
    "/admin/transaction/", "/admin/invoice/", "/admin/invoice/create/",
    "/admin/payment-link/", "/admin/payment-link/create/",
    "/admin/gateways/", "/admin/gateways/create/", "/admin/gateways/create-bank/",
    "/admin/brands/", "/admin/brands/create/",
    "/admin/brand-setting/", "/admin/brand-setting/general/",
    "/admin/brand-setting/api/", "/admin/brand-setting/currency/",
    "/admin/brand-setting/faq/", "/admin/brand-setting/themes/",
    "/admin/devices/", "/admin/devices/balance-verification/",
    "/admin/domains/", "/admin/sms-data/",
    "/admin/staff/", "/admin/staff/create/",
    "/admin/reports/", "/admin/addons/",
    "/admin/my-account/", "/admin/system-settings/",
]


class AdminRenderTests(TestCase):
    def setUp(self):
        _seed_admin(self.client)

    def test_all_admin_pages_render_with_shell(self):
        for url in ADMIN_PAGES:
            with self.subTest(url=url):
                r = self.client.get(url)
                self.assertEqual(r.status_code, 200, url)
                html = r.content.decode()
                self.assertIn('id="sidebarMenu"', html, url)
                self.assertIn("page-wrapper", html, url)

    def test_brand_setting_hub_lists_five_sections(self):
        r = self.client.get("/admin/brand-setting/")
        self.assertEqual(r.status_code, 200)
        for title in ("General Settings", "API Settings", "Theme Settings",
                      "FAQ Settings", "Currency Settings"):
            self.assertContains(r, title)

    def test_brand_setting_hub_respects_resource_permissions(self):
        Admin.objects.create(a_id="s1", full_name="S", username="s", email="s@x.co",
                             password=make_password("pw"), role="staff")
        Permission.objects.create(
            brand_id="b1", a_id="s1",
            permission=json.dumps({"resources": {"brand_settings": {"view": True}},
                                   "pages": {"brand_settings": True}}))
        self.client.get("/login/?logout=1")
        self.client.post("/login/", {"username": "s", "password": "pw"})
        r = self.client.get("/admin/brand-setting/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "General Settings")
        self.assertNotContains(r, "API Settings")
        self.assertNotContains(r, "Currency Settings")

    def test_dashboard_loads_charts_lib_before_inline_script(self):
        html = self.client.get("/admin/dashboard/").content.decode()
        self.assertIn("apexcharts.min.js", html)
        self.assertLess(html.index("apexcharts.min.js"), html.index("drawTxn"))


class CheckoutRenderTests(TestCase):
    def setUp(self):
        Brand.objects.create(brand_id="b1", identify_name="B", name="B")
        Currency.objects.create(brand_id="b1", code="BDT", symbol="t", rate=1)
        # An Admin row must exist or the install gate redirects everything.
        Admin.objects.create(a_id="a1", full_name="R", username="root",
                             email="root@x.co", password=make_password("pw"),
                             role="admin")
        Gateway.objects.create(gateway_id="g9", brand_id="b1", slug="bank", name="Bank",
                               display="Bank", currency="BDT", tab="bank", status="active")
        Transaction.objects.create(brand_id="b1", ref="P9", customer_info="{}",
                                   amount=100, currency="BDT", metadata="[]",
                                   status="initiated")

    def test_payment_page_uses_checkout_theme(self):
        r = self.client.get("/payment/P9/")
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn("pp-checkout-card", html)
        self.assertIn("pp-total-banner", html)

    def test_gateway_verify_form_carries_csrf_token(self):
        html = self.client.get("/payment/P9/?gateway=g9").content.decode()
        self.assertIn("payment-instructions", html)
        self.assertIn("csrfmiddlewaretoken", html)

    def test_verify_endpoint_enforces_csrf(self):
        c = Client(enforce_csrf_checks=True)
        payload = {"gateway-id": "g9", "transaction-id": "P9", "trxid": "abc"}
        r = c.post("/payment/P9/verify/", payload)
        self.assertEqual(r.status_code, 403)
        c.get("/payment/P9/?gateway=g9")
        token = c.cookies["csrftoken"].value
        payload["csrfmiddlewaretoken"] = token
        r2 = c.post("/payment/P9/verify/", payload)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2["Content-Type"], "application/json")


class StaticAssetTests(TestCase):
    ASSETS = [
        "app.css",
        "vendor/css/tabler.min.css", "vendor/css/choices.min.css",
        "vendor/css/inter.css",
        "vendor/js/jquery-3.6.4.min.js", "vendor/js/tabler.min.js",
        "vendor/js/choices.min.js", "vendor/js/qrcode.min.js",
        "vendor/js/custom-toast.js", "vendor/js/apexcharts.min.js",
        "vendor/logo-light.png", "vendor/logo-dark.png",
        "vendor/favicon-light.png", "vendor/favicon-dark.png",
    ]

    def test_all_referenced_assets_exist(self):
        for asset in self.ASSETS:
            with self.subTest(asset=asset):
                self.assertIsNotNone(finders.find(asset), asset)

    def test_whitenoise_serves_static_in_production_mode(self):
        from django.conf import settings
        self.assertIn("whitenoise.middleware.WhiteNoiseMiddleware", settings.MIDDLEWARE)
        self.assertEqual(settings.STORAGES["staticfiles"]["BACKEND"],
                         "whitenoise.storage.CompressedStaticFilesStorage")
