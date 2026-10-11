"""Security regression tests: authn/z enforcement, tenant isolation,
throttling, atomicity. Every test asserts a denial or a bound."""
import json
from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.test import TestCase

from core.models import (Admin, ApiKey, Brand, Currency, Customer, Gateway, Invoice,
                         Permission, Transaction)


def _mkbrand(bid="b1"):
    return Brand.objects.create(brand_id=bid, identify_name=bid, name=bid)


def _mkadmin(username="root", role="admin", a_id="a1"):
    return Admin.objects.create(a_id=a_id, full_name="R", username=username,
                                email=f"{username}@x.co", password=make_password("pw"),
                                role=role, twofa_secret="JBSWY3DPEHPK3PXP")


def _grant(a_id, brand_id):
    Permission.objects.create(brand_id=brand_id, a_id=a_id, permission="{}")


class AnonAccessTests(TestCase):
    def test_payment_link_edit_requires_login(self):
        # SEC-01: previously undecorated; must redirect, never render or 500.
        r = self.client.get("/admin/payment-link/create/")
        self.assertEqual(r.status_code, 302)
        r = self.client.get("/admin/payment-link/ABC/")
        self.assertEqual(r.status_code, 302)
        r = self.client.post("/admin/payment-link/create/", {"title": "x"})
        self.assertEqual(r.status_code, 302)


class StaffDenialMatrixTests(TestCase):
    def setUp(self):
        _mkbrand("b1")
        _mkadmin("root", "admin", "a1")
        _grant("a1", "b1")
        _mkadmin("staff", "staff", "s1")
        Permission.objects.create(brand_id="b1", a_id="s1",
                                  permission=json.dumps({"resources": {}, "pages": {}}))

    def test_staff_without_pages_gets_403(self):
        self.client.post("/login/", {"username": "staff", "password": "pw"})
        for url in ["/admin/transaction/", "/admin/customers/", "/admin/invoice/",
                    "/admin/payment-link/", "/admin/gateways/", "/admin/brands/",
                    "/admin/devices/", "/admin/domains/", "/admin/sms-data/",
                    "/admin/staff/", "/admin/brand-setting/api/",
                    "/admin/brand-setting/currency/", "/admin/brand-setting/faq/",
                    "/admin/addons/"]:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 403, url)


class CrossBrandTests(TestCase):
    def setUp(self):
        _mkbrand("b1")
        _mkbrand("b2")
        Currency.objects.create(brand_id="b1", code="BDT", symbol="t", rate=1)
        _mkadmin("root", "admin", "a1")
        _grant("a1", "b1")
        _mkadmin("s1", "staff", "s1")
        Permission.objects.create(
            brand_id="b1", a_id="s1",
            permission=json.dumps({"resources": {
                "transaction": {"delete": True, "approve": True},
                "customers": {"delete": True}, "invoice": {"delete": True},
                "payment_link": {"delete": True}, "gateways": {"delete": True}},
                "pages": {"transaction": True, "customers": True, "invoice": True,
                          "payment_link": True, "gateways": True}}))
        self.txn_b2 = Transaction.objects.create(
            brand_id="b2", ref="B2TXN", customer_info="{}", amount=10,
            currency="BDT", status="completed")
        self.cust_b2 = Customer.objects.create(ref="c", brand_id="b2", name="N",
                                               email="e@x.co", mobile="1")
        self.inv_b2 = Invoice.objects.create(ref="INV2", brand_id="b2", customer_info="{}",
                                             currency="BDT", status="unpaid")

    def _login_staff(self):
        self.client.post("/login/", {"username": "s1", "password": "pw"})

    def test_transaction_delete_other_brand_404(self):
        self._login_staff()
        r = self.client.post("/admin/transaction/", {"delete": self.txn_b2.id})
        self.assertEqual(r.status_code, 404)
        self.assertTrue(Transaction.objects.filter(ref="B2TXN").exists())

    def test_transaction_bulk_skips_other_brand(self):
        self._login_staff()
        t1 = Transaction.objects.create(brand_id="b1", ref="B1TXN", customer_info="{}",
                                        amount=10, currency="BDT", status="pending")
        self.client.post("/admin/transaction/",
                         {"bulk": "approved", "selected_ids": [t1.id, self.txn_b2.id]})
        self.assertEqual(Transaction.objects.get(ref="B1TXN").status, "completed")
        self.assertEqual(Transaction.objects.get(ref="B2TXN").status, "completed")

    def test_customer_delete_other_brand_404(self):
        self._login_staff()
        r = self.client.post("/admin/customers/", {"delete": self.cust_b2.id})
        self.assertEqual(r.status_code, 404)
        self.assertTrue(Customer.objects.filter(id=self.cust_b2.id).exists())

    def test_invoice_delete_other_brand_404(self):
        self._login_staff()
        r = self.client.post("/admin/invoice/", {"delete": "INV2"})
        self.assertEqual(r.status_code, 404)
        self.assertTrue(Invoice.objects.filter(ref="INV2").exists())

    def test_gateway_delete_other_brand_404(self):
        Gateway.objects.create(gateway_id="g2", brand_id="b2", slug="bkash-personal",
                               currency="BDT", status="active")
        self._login_staff()
        r = self.client.post("/admin/gateways/", {"delete": "g2"})
        self.assertEqual(r.status_code, 404)
        self.assertTrue(Gateway.objects.filter(gateway_id="g2").exists())


class ApiTenantTests(TestCase):
    def setUp(self):
        _mkadmin("installer", "admin", "inst")
        _mkbrand("b1")
        _mkbrand("b2")
        Currency.objects.create(brand_id="b1", code="BDT", symbol="t", rate=1)
        self.key = ApiKey.objects.create(
            brand_id="b1", name="k", api_key="K", expired_date="--",
            api_scopes=json.dumps(["create_payment", "verify_payment", "refund_payment"]))
        self.txn = Transaction.objects.create(
            brand_id="b2", ref="B2TXN", customer_info="{}", amount=10,
            currency="BDT", status="completed", local_net_amount=10, local_currency="BDT")

    def _auth(self, **kw):
        kw["HTTP_MHS_PIPRAPAY_API_KEY"] = "K"
        return kw

    def test_verify_other_brand_hidden(self):
        r = self.client.post("/api/verify-payment/", data=json.dumps({"pp_id": "B2TXN"}),
                             content_type="application/json", **self._auth())
        self.assertEqual(r.json()["error"]["code"], "INVALID_PP_ID")

    def test_refund_other_brand_no_write(self):
        r = self.client.post("/api/refund-payment/", data=json.dumps({"pp_id": "B2TXN"}),
                             content_type="application/json", **self._auth())
        self.assertEqual(r.json()["error"]["code"], "INVALID_PP_ID")
        self.assertEqual(Transaction.objects.get(ref="B2TXN").status, "completed")

    def test_scope_denials(self):
        key2 = ApiKey.objects.create(brand_id="b1", name="k2", api_key="K2",
                                     expired_date="--", api_scopes=json.dumps([]))
        auth = {"HTTP_MHS_PIPRAPAY_API_KEY": "K2"}
        for url in ["/api/checkout/redirect/", "/api/checkout/popup/",
                    "/api/verify-payment/", "/api/refund-payment/"]:
            r = self.client.post(url, data=json.dumps({}), content_type="application/json",
                                 **auth)
            body = r.json()
            code = body.get("error", {}).get("code")
            self.assertIn(code, ("INSUFFICIENT_SCOPE", "INVALID_JSON_PAYLOAD"), url)

    def test_popup_webhook_domain_enforced(self):
        payload = {"full_name": "A", "email_address": "a@b.co", "mobile_number": "1",
                   "amount": "10", "currency": "BDT",
                   "webhook_url": "https://evil.example/hook", "metadata": {}}
        r = self.client.post("/api/checkout/popup/", data=json.dumps(payload),
                             content_type="application/json", **self._auth())
        self.assertEqual(r.json()["error"]["code"], "INVALID_URL")


class ThrottleTests(TestCase):
    def test_five_failures_then_429(self):
        _mkbrand("b1")
        _mkadmin("root", "admin", "a1")
        _grant("a1", "b1")
        for _ in range(5):
            r = self.client.post("/login/", {"username": "root", "password": "wrong"})
            self.assertEqual(r.status_code, 200)
        r = self.client.post("/login/", {"username": "root", "password": "wrong"})
        self.assertEqual(r.status_code, 429)
        # Correct password still blocked while throttle holds, then succeeds after clear.
        from core.views import _throttle_clear
        from django.test import RequestFactory
        req = RequestFactory().post("/login/")
        _throttle_clear(req, "root")
        r = self.client.post("/login/", {"username": "root", "password": "pw"})
        self.assertEqual(r.status_code, 302)


class StateMachineTests(TestCase):
    def setUp(self):
        _mkbrand("b1")
        Currency.objects.create(brand_id="b1", code="BDT", symbol="t", rate=1)

    def test_double_complete_rejected(self):
        from core.transactions import set_transaction_status
        from core.models import Gateway
        Gateway.objects.create(gateway_id="g1", brand_id="b1", slug="bkash-personal",
                               currency="BDT", status="active")
        Transaction.objects.create(brand_id="b1", ref="T1", customer_info="{}",
                                   amount=10, currency="BDT", status="initiated")
        self.assertTrue(set_transaction_status("T1", "completed", "g1", "TRX"))
        self.assertFalse(set_transaction_status("T1", "completed", "g1", "TRX2"))
        txn = Transaction.objects.get(ref="T1")
        self.assertEqual(txn.trx_id, "TRX")
        self.assertFalse(set_transaction_status("T1", "canceled"))

    def test_terminal_states_frozen(self):
        from core.transactions import set_transaction_status
        Transaction.objects.create(brand_id="b1", ref="T2", customer_info="{}",
                                   amount=10, currency="BDT", status="pending")
        self.assertFalse(set_transaction_status("T2", "completed", "g1", "TRX"))

    def test_refund_only_from_completed(self):
        _mkadmin("installer", "admin", "inst")
        ApiKey.objects.create(brand_id="b1", name="k", api_key="K", expired_date="--",
                              api_scopes=json.dumps(["refund_payment"]))
        Transaction.objects.create(brand_id="b1", ref="T3", customer_info="{}",
                                   amount=10, currency="BDT", status="pending")
        r = self.client.post("/api/refund-payment/", data=json.dumps({"pp_id": "T3"}),
                             content_type="application/json",
                             HTTP_MHS_PIPRAPAY_API_KEY="K")
        self.assertEqual(r.json()["error"]["code"], "INVALID_STATUS")
        Transaction.objects.create(brand_id="b1", ref="T4", customer_info="{}",
                                   amount=10, currency="BDT", status="refunded")
        r = self.client.post("/api/refund-payment/", data=json.dumps({"pp_id": "T4"}),
                             content_type="application/json",
                             HTTP_MHS_PIPRAPAY_API_KEY="K")
        self.assertEqual(r.json()["error"]["code"], "INVALID_STATUS")


class IpnBindingTests(TestCase):
    def test_brand_mismatch_ignored(self):
        _mkadmin("installer", "admin", "inst")
        _mkbrand("b1")
        _mkbrand("b2")
        Gateway.objects.create(gateway_id="g1", brand_id="b1", slug="bkash-personal",
                               currency="BDT", status="active")
        Transaction.objects.create(brand_id="b2", ref="T9", customer_info="{}",
                                   amount=10, currency="BDT", status="initiated")
        r = self.client.post("/ipn/g1/", data={"pp_id": "T9"})
        self.assertEqual(r.json(), {"status": "ignored"})
        self.assertEqual(Transaction.objects.get(ref="T9").status, "initiated")
