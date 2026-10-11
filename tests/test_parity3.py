"""Third-wave parity tests: rounded API totals, flat display VAT, webhook shape,
theme slug keys, bank fallback, cancel semantics, validation exactness,
env semantics, bcrypt, cascades, redirects."""
import json

from django.contrib.auth.hashers import make_password
from django.test import TestCase

from core import money as M
from core.env import get_env, set_env
from core.models import (Admin, ApiKey, Brand, Currency, Customer, Domain, EnvOption,
                         Gateway, Invoice, InvoiceItem, PaymentLink, Permission, Transaction)
from core.transactions import handle_invoice_webhook, invoice_display_totals, transaction_payload


def _seed():
    Brand.objects.create(brand_id="b1", identify_name="B", name="B")
    Currency.objects.create(brand_id="b1", code="BDT", symbol="t", rate=1)
    if not Admin.objects.filter(username="installer").exists():
        Admin.objects.create(a_id="admx", full_name="A", username="installer",
                             email="i@x.co", password=make_password("pw"), role="admin")
    Admin.objects.create(a_id="a1", full_name="R", username="root", email="r@x.com",
                         password=make_password("pw"), role="admin")
    Permission.objects.create(brand_id="b1", a_id="a1", permission="{}")


class ApiPayloadTests(TestCase):
    def test_total_rounded_and_empty_preserved(self):
        _seed()
        t = Transaction.objects.create(brand_id="b1", ref="T1",
                                       customer_info=json.dumps({"name": "", "email": None}),
                                       amount="10.005", processing_fee="0.005",
                                       discount_amount="0.003", currency="BDT",
                                       local_net_amount=1, local_currency="BDT", status="completed")
        api = transaction_payload(t, round_total=True)
        self.assertEqual(api["total"], "10.00")
        self.assertEqual(api["full_name"], "")
        self.assertEqual(api["email_address"], "N/A")
        raw = transaction_payload(t)
        self.assertEqual(raw["total"], "10.00700000")

    def test_explicit_dash_url_rejected_missing_accepted(self):
        _seed()
        ApiKey.objects.create(brand_id="b1", name="k", api_key="K", expired_date="--",
                              api_scopes=json.dumps(["create_payment"]))
        Domain.objects.create(domain="example.com", status="active")
        base = {"full_name": "A", "email_address": "a@b.co", "mobile_number": "1",
                "amount": "10", "currency": "BDT", "metadata": {}}
        p = dict(base, return_url="--")
        r = self.client.post("/api/checkout/redirect/", data=json.dumps(p),
                             content_type="application/json", HTTP_MHS_PIPRAPAY_API_KEY="K")
        self.assertEqual(r.json()["error"]["code"], "INVALID_URL")
        p = dict(base, return_url="https://example.com/x")
        r = self.client.post("/api/checkout/redirect/", data=json.dumps(p),
                             content_type="application/json", HTTP_MHS_PIPRAPAY_API_KEY="K")
        self.assertEqual(r.status_code, 200)

    def test_metadata_empty_string_and_list(self):
        _seed()
        ApiKey.objects.create(brand_id="b1", name="k", api_key="K", expired_date="--",
                              api_scopes=json.dumps(["create_payment"]))
        base = {"full_name": "A", "email_address": "a@b.co", "mobile_number": "1",
                "amount": "10", "currency": "BDT"}
        r = self.client.post("/api/checkout/redirect/", data=json.dumps(dict(base, metadata="")),
                             content_type="application/json", HTTP_MHS_PIPRAPAY_API_KEY="K")
        self.assertEqual(r.json()["error"]["code"], "INVALID_JSON")
        r = self.client.post("/api/checkout/redirect/", data=json.dumps(dict(base, metadata=[1, 2])),
                             content_type="application/json", HTTP_MHS_PIPRAPAY_API_KEY="K")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(json.loads(Transaction.objects.get().metadata), [1, 2])

    def test_scientific_and_padded_amounts(self):
        _seed()
        ApiKey.objects.create(brand_id="b1", name="k", api_key="K", expired_date="--",
                              api_scopes=json.dumps(["create_payment"]))
        base = {"full_name": "A", "email_address": "a@b.co", "mobile_number": "1",
                "currency": "BDT", "metadata": {}}
        for amount in ("1e1", " 10 "):
            r = self.client.post("/api/checkout/redirect/", data=json.dumps(dict(base, amount=amount)),
                                 content_type="application/json", HTTP_MHS_PIPRAPAY_API_KEY="K")
            self.assertEqual(r.status_code, 200, amount)


class InvoiceDisplayTests(TestCase):
    def test_flat_vat_on_display(self):
        _seed()
        Invoice.objects.create(ref="I1", brand_id="b1", customer_info="{}", currency="BDT",
                               status="unpaid")
        InvoiceItem.objects.create(brand_id="b1", invoice_id="I1", description="x",
                                   amount="100", quantity=2, discount="10", vat="15")
        lines, sums = invoice_display_totals(list(InvoiceItem.objects.filter(invoice_id="I1")))
        self.assertEqual(lines[0]["total"], "205.00")
        self.assertEqual(lines[0]["vat"], "15.00")
        r = self.client.get("/invoice/I1/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "205.00")

    def test_webhook_exact_shape(self):
        _seed()
        Invoice.objects.create(ref="I9", brand_id="b1",
                               customer_info=json.dumps({"name": "N"}),
                               currency="BDT", status="unpaid", shipping="5")
        Transaction.objects.create(brand_id="b1", ref="TX9", customer_info="{}", amount=50,
                                   currency="BDT", status="completed",
                                   metadata=json.dumps({"invoice_id": "I9"}))
        seen = {}
        from core import hooks
        hooks.add_action("invoices.updated.status",
                         lambda payload: seen.update(payload=payload), priority=5)
        try:
            code, body = handle_invoice_webhook({"pp_id": "TX9"})
        finally:
            from core.hooks import _actions
            _actions["invoices.updated.status"] = [
                (p, cb) for p, cb in _actions["invoices.updated.status"] if p != 5]
        self.assertEqual((code, body), (200, "OK"))
        payload = seen["payload"][0]
        self.assertIsInstance(payload["customer_info"], str)
        self.assertIsNone(payload["invoice_info"]["due_date"])
        self.assertEqual(payload["invoice_info"]["shipping"], "5.00")
        self.assertEqual(payload["invoice_items"], [])
        self.assertEqual(Invoice.objects.get(ref="I9").status, "paid")
        self.assertEqual(Invoice.objects.get(ref="I9").gateway_id, "--")


class CheckoutMiscTests(TestCase):
    def test_theme_slug_keys(self):
        _seed()
        from checkout.views import _theme_options
        set_env("twenty-six-primary_color", "#123456", "b1")
        opts = _theme_options(Brand.objects.get(brand_id="b1"))
        self.assertEqual(opts["primary_color"], "#123456")

    def test_bank_fallback_slip(self):
        _seed()
        Gateway.objects.create(gateway_id="g9", brand_id="b1", slug="bank", name="Bank",
                               display="Bank", currency="BDT", tab="bank", status="active")
        Transaction.objects.create(brand_id="b1", ref="P9", customer_info="{}",
                                   amount=100, currency="BDT", metadata="[]", status="initiated")
        html = self.client.get("/payment/P9/?gateway=g9").content.decode()
        self.assertIn('name="slip"', html)
        self.assertIn("payment-instructions", html)

    def test_invalid_gateway_403(self):
        _seed()
        Transaction.objects.create(brand_id="b1", ref="P9", customer_info="{}",
                                   amount=100, currency="BDT", metadata="[]", status="initiated")
        r = self.client.get("/payment/P9/?gateway=nope")
        self.assertEqual(r.status_code, 403)

    def test_receipt_alias(self):
        _seed()
        Transaction.objects.create(brand_id="b1", ref="P9", customer_info="{}",
                                   amount=100, currency="BDT", metadata="[]", status="completed")
        self.assertEqual(self.client.get("/payment/P9/?receipt=1").status_code, 200)

    def test_lang_redirect_keeps_gateway(self):
        _seed()
        Gateway.objects.create(gateway_id="g9", brand_id="b1", slug="bkash-personal",
                               currency="BDT", status="active")
        Transaction.objects.create(brand_id="b1", ref="P9", customer_info="{}",
                                   amount=100, currency="BDT", metadata="[]", status="initiated")
        r = self.client.get("/payment/P9/?gateway=g9&lang=bn")
        self.assertEqual(r.status_code, 302)
        self.assertIn("gateway=g9", r["Location"])
        self.assertNotIn("lang=", r["Location"])


class EnvSemanticsTests(TestCase):
    def test_miss_returns_empty_and_inserts(self):
        self.assertEqual(get_env("nope-key"), "")
        self.assertTrue(EnvOption.objects.filter(option_name="nope-key", value="--").exists())

    def test_no_both_fallback(self):
        set_env("k", "global", "both")
        self.assertEqual(get_env("k", "b1"), "")

    def test_is_expired_edges(self):
        self.assertFalse(M.is_expired("0"))
        self.assertFalse(M.is_expired("2999-01-01"))
        self.assertTrue(M.is_expired("2000-01-01"))

    def test_time_ago_future(self):
        self.assertEqual(M.time_ago("2999-01-01 00:00:00"), "Just now")

    def test_bcrypt_default(self):
        from django.contrib.auth.hashers import make_password as mp
        self.assertTrue(mp("x").startswith("bcrypt"))


class StaffBehaviorTests(TestCase):
    def test_brand_cascade_and_guards(self):
        _seed()
        Customer.objects.create(ref="c", brand_id="b1", name="N", email="e@x.co", mobile="1")
        self.client.post("/login/", {"username": "root", "password": "pw"})
        first = Brand.objects.order_by("id").first()
        self.client.post("/admin/brands/", {"delete": first.brand_id})
        self.assertTrue(Brand.objects.filter(brand_id="b1").exists())
        self.client.post("/admin/brands/create/", {"identify_name": "Second"})
        b2 = Brand.objects.get(identify_name="Second")
        self.client.post("/set-brand/", {"brand_id": "b1"})
        self.client.post("/admin/brands/", {"delete": b2.brand_id})
        self.assertFalse(Brand.objects.filter(brand_id=b2.brand_id).exists())
        self.assertFalse(Customer.objects.filter(brand_id=b2.brand_id).exists())

    def test_staff_create_requires_password_and_brand(self):
        _seed()
        self.client.post("/login/", {"username": "root", "password": "pw"})
        r = self.client.post("/admin/staff/create/",
                             {"full_name": "S", "username": "u", "email": "u@x.co"})
        self.assertFalse(Admin.objects.filter(username="u").exists())
        r = self.client.post("/admin/staff/create/",
                             {"full_name": "S", "username": "u", "email": "u@x.co",
                              "password": "pw123456", "brands": ["b1"]})
        self.assertTrue(Admin.objects.filter(username="u").exists())

    def test_domain_normalize_and_dup(self):
        _seed()
        self.client.post("/login/", {"username": "root", "password": "pw"})
        self.client.post("/admin/domains/", {"domain": "WWW.Example.COM", "status": "active"})
        self.assertTrue(Domain.objects.filter(domain="example.com").exists())
        self.client.post("/admin/domains/", {"domain": "example.com", "status": "active"})
        self.assertEqual(Domain.objects.filter(domain="example.com").count(), 1)
        self.client.post("/admin/domains/", {"domain": "not a url!!", "status": "active"})
        self.assertEqual(Domain.objects.count(), 1)

    def test_cron_key_hex(self):
        _seed()
        self.client.post("/login/", {"username": "root", "password": "pw"})
        self.client.post("/admin/system-settings/", {"op": "cron-regenerate"})
        token = get_env("cron-job")
        self.assertEqual(len(token), 16)
        int(token, 16)

    def test_companion_dup_code103(self):
        _seed()
        self.client.post("/login/", {"username": "root", "password": "pw"})
        otp = self.client.post("/admin/devices/connect-info/").json()["otp"]
        token = self.client.post("/api/companion/login/", {"onetimepassword": otp}).json()["token"]
        msg = ("You have received Tk 500.00 from 01700000000. Fee Tk 0.00. "
               "Balance Tk 10,500.00. TrxID DUP1AAAAA at 2024/01/01 12:00")
        body = {"token": token, "sms_list": json.dumps([{"sender": "bkash", "message": msg}])}
        self.client.post("/api/companion/sms-transmit-bulk/", body)
        self.client.post("/api/companion/sms-transmit-bulk/", body)
        from core.models import SmsData
        self.assertTrue(SmsData.objects.filter(status="error",
                                               reason__contains="Code 103").exists())


class DisplayFilterTests(TestCase):
    def test_pp_filters(self):
        from django.template import Context, Template
        out = Template("{% load pp_display %}{{ a|pp }}|{{ b|pp }}|{{ c|pp_blank }}").render(
            Context({"a": None, "b": "", "c": "--"}))
        self.assertEqual(out, "--||")

    def test_set_env_returns_value(self):
        from core.env import set_env
        self.assertEqual(set_env("k", "v"), "v")

    def test_get_param_str_only(self):
        from core.money import get_param
        self.assertIsNone(get_param({"k": 123}, "k"))
        self.assertEqual(get_param({"k": "ab-12_X"}, "k"), "ab-12_X")
        self.assertIsNone(get_param({"k": "a b"}, "k"))

    def test_ipn_uses_gateway_langs(self):
        Admin.objects.create(a_id="a", full_name="A", username="u", email="u@x.co",
                             password=make_password("pw"), role="admin")
        Brand.objects.create(brand_id="b1", identify_name="B", name="B", language="fr")
        Gateway.objects.create(gateway_id="g1", brand_id="b1", slug="bkash-personal",
                               currency="BDT", status="active")
        r = self.client.post("/ipn/g1/", data={"pp_id": "ZZZ"})
        # fr unsupported by bkash (en/bn) -> resolved en; unknown txn -> ignored.
        self.assertEqual(r.json(), {"status": "ignored"})
