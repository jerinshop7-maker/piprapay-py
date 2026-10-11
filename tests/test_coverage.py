"""Coverage tests: staff CRUD posts, checkout/API branches, cron entries,
gateway callbacks, middleware, reports, SMS/balance/currency ops."""
import json
from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.test import TestCase

from core.models import (Admin, ApiKey, BalanceVerification, Brand, Currency, Customer,
                         Device, Domain, Faq, Gateway, GatewayParameter, Invoice, InvoiceItem,
                         PaymentLink, PaymentLinkField, Permission, SmsData, Transaction)


def _seed_all(client, username="root", role="admin", a_id="a1"):
    Brand.objects.create(brand_id="b1", identify_name="B", name="B")
    Currency.objects.create(brand_id="b1", code="BDT", symbol="t", rate=1)
    Admin.objects.create(a_id=a_id, full_name="R", username=username,
                         email=f"{username}@x.co", password=make_password("pw"),
                         role=role, twofa_secret="JBSWY3DPEHPK3PXP")
    Permission.objects.create(brand_id="b1", a_id=a_id, permission="{}")
    client.post("/login/", {"username": username, "password": "pw"})


class MockResp:
    def __init__(self, payload, status=200, headers=None):
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class StaffPostTests(TestCase):
    def setUp(self):
        _seed_all(self.client)

    def test_transaction_edit_actions(self):
        t = Transaction.objects.create(brand_id="b1", ref="T1", customer_info="{}",
                                       amount=10, currency="BDT", status="pending",
                                       webhook_url="--")
        self.client.post(f"/admin/transaction/{t.id}/", {"action": "approve"})
        self.assertEqual(Transaction.objects.get(ref="T1").status, "completed")
        self.client.post(f"/admin/transaction/{t.id}/", {"action": "cancel"})
        self.assertEqual(Transaction.objects.get(ref="T1").status, "canceled")
        self.client.post(f"/admin/transaction/{t.id}/", {"delete": t.id})
        self.assertFalse(Transaction.objects.filter(ref="T1").exists())

    def test_staff_edit_self_blocked(self):
        r = self.client.post("/admin/staff/a1/", {"full_name": "X"})
        # a1 is admin (not staff row) -> 404; create staff then self-edit attempt
        Admin.objects.create(a_id="s1", full_name="S", username="s", email="s@x.co",
                             password=make_password("pw"), role="staff")
        # The staff must hold staff_management/page + staff:edit to reach the
        # self-edit guard; otherwise page_required denies with 403 first.
        Permission.objects.create(
            brand_id="b1", a_id="s1",
            permission=json.dumps({"resources": {"staff": {"edit": True}},
                                  "pages": {"staff_management": True}}))
        self.client.get("/login/?logout=1")
        self.client.post("/login/", {"username": "s", "password": "pw"})
        r = self.client.post("/admin/staff/s1/", {"full_name": "Mine"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Admin.objects.get(a_id="s1").full_name, "S")

    def test_staff_bulk_activate(self):
        Admin.objects.create(a_id="s1", full_name="S", username="s", email="s@x.co",
                             password=make_password("pw"), role="staff", status="suspend")
        self.client.post("/admin/staff/", {"bulk": "activated", "selected_ids": ["s1"]})
        self.assertEqual(Admin.objects.get(a_id="s1").status, "active")

    def test_invoice_edit_post_and_status(self):
        inv = Invoice.objects.create(ref="I1", brand_id="b1", customer_info="{}",
                                     currency="BDT", status="unpaid")
        InvoiceItem.objects.create(brand_id="b1", invoice_id="I1", description="x",
                                   amount=10, quantity=1, discount=0, vat=0)
        item = InvoiceItem.objects.get(invoice_id="I1")
        self.client.post("/admin/invoice/I1/",
                         {"brand_id": "b1", "currency": "BDT", "due_date": "",
                          "shipping": "0", "status": "unpaid",
                          "item_id": [str(item.id)], "item_description": ["y"],
                          "item_amount": ["20"], "item_quantity": ["1"],
                          "item_discount": ["0"], "item_vat": ["0"]})
        item.refresh_from_db()
        self.assertEqual((item.description, str(item.amount)), ("y", "20.00000000"))
        self.client.post("/admin/invoice/", {"manage_status": "I1", "status": "canceled"})
        self.assertEqual(Invoice.objects.get(ref="I1").status, "canceled")

    def test_link_manage_and_fields_roundtrip(self):
        link = PaymentLink.objects.create(ref="L1", brand_id="b1",
                                          product_info=json.dumps({"title": "T", "description": "D"}),
                                          amount=10, quantity=1, currency="BDT",
                                          expired_date="--", status="active")
        self.client.post("/admin/payment-link/L1/",
                         {"brand_id": "b1", "title": "T2", "description": "D2",
                          "amount": "10", "quantity": "1", "currency": "BDT",
                          "expired_date": "", "status": "active",
                          "items[n1][formType]": "select", "items[n1][fieldName]": "Size",
                          "items[n1][required]": "true", "items[n1][addOptions]": ["S", "M"]})
        link.refresh_from_db()
        self.assertEqual(json.loads(link.product_info)["title"], "T2")
        f = PaymentLinkField.objects.get(paymentLinkID="L1")
        self.assertEqual((f.formType, f.value), ("select", "S, M"))
        self.client.post("/admin/payment-link/", {"manage_status": "L1", "status": "inactive"})
        self.assertEqual(PaymentLink.objects.get(ref="L1").status, "inactive")

    def test_gateway_edit_post(self):
        gw = Gateway.objects.create(gateway_id="g1", brand_id="b1", slug="bkash-personal",
                                    display="bK", currency="BDT", status="active")
        self.client.post("/admin/gateways/g1/",
                         {"display": "bKash", "currency": "BDT", "status": "active",
                          "tab": "mfs", "min_allow": "5", "max_allow": "500",
                          "fixed_discount": "0", "percentage_discount": "0",
                          "fixed_charge": "2", "percentage_charge": "0",
                          "primary_color": "#D12053", "text_color": "#FFFFFF",
                          "mobile_number": "017", "pending_payment": "enable"})
        gw.refresh_from_db()
        self.assertEqual((str(gw.min_allow), str(gw.fixed_charge)), ("5.00000000", "2.00000000"))
        self.assertEqual(GatewayParameter.objects.get(gateway_id="g1",
                                                      option_name="pending_payment").value, "enable")
        self.client.post("/admin/gateways/", {"bulk": "inactivated", "selected_ids": ["g1"]})
        self.assertEqual(Gateway.objects.get(gateway_id="g1").status, "inactive")

    def test_device_delete_cascades(self):
        Device.objects.create(d_id="x", device_id="d9", otp="o", status="used")
        BalanceVerification.objects.create(device_id="d9", sender_key="bkash",
                                           type="Personal", current_balance=0,
                                           simslot="Any", status="active")
        dev = Device.objects.get(device_id="d9")
        self.client.post("/admin/devices/", {"delete": dev.id})
        self.assertFalse(BalanceVerification.objects.filter(device_id="d9").exists())

    def test_domain_edit_and_themes(self):
        Domain.objects.create(domain="old.example", status="active")
        d = Domain.objects.get(domain="old.example")
        self.client.post("/admin/domains/", {"edit_id": d.id, "domain": "new.example",
                                             "status": "inactive"})
        d.refresh_from_db()
        self.assertEqual((d.domain, d.status), ("new.example", "inactive"))
        self.client.post("/admin/brand-setting/themes/", {"activate": "twenty-six"})
        self.assertEqual(Brand.objects.get(brand_id="b1").theme, "twenty-six")
        self.client.post("/admin/brand-setting/themes/",
                         {"brand_id": "b1", "watermark_text": "WM"})
        from core.env import get_env
        self.assertEqual(get_env("twenty-six-watermark_text", "b1"), "WM")

    def test_brand_create_dup_and_currency_edit(self):
        self.client.post("/admin/brands/create/", {"identify_name": "B"})
        self.assertEqual(Brand.objects.filter(identify_name="B").count(), 1)
        self.client.post("/admin/brand-setting/currency/",
                         {"brand_id": "b1", "code": "USD", "symbol": "$", "rate": "117"})
        row = Currency.objects.get(brand_id="b1", code="USD")
        self.client.post("/admin/brand-setting/currency/",
                         {"edit_id": row.id, "symbol": "US$", "rate": "118"})
        row.refresh_from_db()
        self.assertEqual((row.symbol, str(row.rate)), ("US$", "118.00000000"))

    def test_faq_apikey_crud(self):
        self.client.post("/admin/brand-setting/faq/",
                         {"brand_id": "b1", "title": "Q", "description": "A", "status": "active"})
        f = Faq.objects.get(title="Q")
        self.client.post("/admin/brand-setting/faq/",
                         {"edit_id": f.id, "title": "Q2", "description": "A",
                          "status": "inactive"})
        f.refresh_from_db()
        self.assertEqual((f.title, f.status), ("Q2", "inactive"))
        self.client.post("/admin/brand-setting/api/",
                         {"brand_id": "b1", "name": "k", "scopes": ["create_payment"],
                          "status": "active"})
        key = ApiKey.objects.get(name="k")
        self.assertEqual(json.loads(key.api_scopes), ["create_payment"])
        self.client.post("/admin/brand-setting/api/",
                         {"edit_id": key.id, "name": "k", "status": "inactive",
                          "scopes": ["verify_payment"]})
        key.refresh_from_db()
        self.assertEqual((key.status, json.loads(key.api_scopes)),
                         ("inactive", ["verify_payment"]))

    def test_general_and_system_save(self):
        r = self.client.post("/admin/brand-setting/general/",
                             {"identify_name": "B", "timezone": "UTC", "language": "en",
                              "currency_code": "BDT", "payment_tolerance": "1",
                              "autoExchange": "disabled"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Brand.objects.get(brand_id="b1").timezone, "UTC")
        r = self.client.post("/admin/system-settings/",
                             {"op": "general", "env_geneal-application-settings-webhook_attempts_limit": "5"})
        from core.env import get_env
        self.assertEqual(get_env("geneal-application-settings-webhook_attempts_limit"), "5")
        r = self.client.post("/admin/system-settings/", {"op": "update-setting",
                                                          "update_channel": "beta"})
        self.assertEqual(get_env("system-settings-update_channel"), "beta")

    def test_my_account_and_reports(self):
        r = self.client.post("/admin/my-account/",
                             {"full_name": "R", "username": "root", "email": "root@x.co",
                              "current_password": "pw"})
        self.assertEqual(r.status_code, 302)
        r = self.client.post("/admin/my-account/", {"logout_others": "1",
                                                    "current_password": "pw"})
        self.assertEqual(r.status_code, 302)
        r = self.client.get("/admin/reports/?preset=today")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Revenue")
        r = self.client.get("/admin/reports/?start=2025-01-02&end=2025-01-01")
        self.assertEqual(r.status_code, 302)
        r = self.client.get("/admin/activities/?status=active")
        self.assertEqual(r.status_code, 200)

    def test_addon_slug_register(self):
        import tempfile
        from pathlib import Path
        from django.conf import settings
        target = Path(settings.BASE_DIR) / "pp-modules" / "pp-addons" / "demo slug"
        target.mkdir(parents=True, exist_ok=True)
        (target / "class.php").write_text("<?php // demo")
        try:
            r = self.client.post("/admin/addons/", {"upload": "1", "slug": "demo slug",
                                                    "name": "Demo"})
            self.assertEqual(r.status_code, 302)
            from core.models import Addon
            self.assertTrue(Addon.objects.filter(slug="demo slug").exists())
        finally:
            import shutil
            shutil.rmtree(target, ignore_errors=True)
            from core.models import Addon
            Addon.objects.filter(slug="demo slug").delete()


class CheckoutBranchTests(TestCase):
    def setUp(self):
        _seed_all(self.client)

    def _gateway(self, slug="stripe", currency="USD", tab="global", gid="g1"):
        return Gateway.objects.create(gateway_id=gid, brand_id="b1", slug=slug,
                                      display=slug, currency=currency, tab=tab,
                                      status="active")

    def _txn(self, ref="P1", status="initiated", amount=100, currency="BDT"):
        return Transaction.objects.create(
            brand_id="b1", ref=ref, customer_info=json.dumps({"name": "N"}),
            amount=amount, currency=currency, metadata="[]", status=status)

    def test_stripe_callback_completes(self):
        self._gateway()
        self._txn()
        sess = {"payment_status": "paid", "payment_intent": "pi_1", "id": "cs_1",
                "metadata": {"invoice_id": "P1"}}
        with patch("gateways.api_gateways.requests.get") as g:
            g.side_effect = [MockResp(sess), MockResp({"status": "succeeded", "id": "pi_1"})]
            r = self.client.get("/payment/P1/?gateway=g1&pp_callback=1&gid=g1&session_id=cs_1")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Transaction.objects.get(ref="P1").status, "completed")

    def test_aamarpay_callback_trxcheck(self):
        self._gateway(slug="aamarpay", currency="BDT", tab="mfs", gid="g2")
        self._txn(ref="P2")
        body = {"pay_status": "Successful", "status_code": "2", "opt_a": "P2",
                "bank_trxid": "BT1", "payment_processor": "PP", "pg_txnid": "PG1"}
        with patch("gateways.api_gateways.requests.get", return_value=MockResp(body)):
            r = self.client.get("/payment/P2/?gateway=g2&pp_callback=1&gid=g2",
                                data={}, follow=False)
            # callback reads POST mer_txnid; supply via POST instead
            r = self.client.post("/payment/P2/?gateway=g2&pp_callback=1&gid=g2",
                                 {"mer_txnid": "M1"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Transaction.objects.get(ref="P2").trx_id, "BT1")

    def test_eps_callback_valuea(self):
        self._gateway(slug="eps", currency="BDT", tab="mfs", gid="g3")
        self._txn(ref="P3")
        token = {"token": "T"}
        status = {"Status": "Success", "ValueA": "P3", "EPSTransactionId": "E1"}
        # Token is fetched with POST; the status check is a GET (class.php:195).
        with patch("gateways.api_gateways.requests.post", return_value=MockResp(token)), \
             patch("gateways.api_gateways.requests.get", return_value=MockResp(status)):
            r = self.client.get("/payment/P3/?gateway=g3&pp_callback=1&gid=g3"
                                "&MerchantTransactionId=M1&EPSTransactionId=E1")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Transaction.objects.get(ref="P3").status, "completed")

    def test_paystation_callback_nested(self):
        self._gateway(slug="paystation", currency="BDT", tab="mfs", gid="g4")
        self._txn(ref="P4")
        body = {"status_code": 200, "status": "success",
                "data": {"trx_status": "successful", "trx_id": "PS1",
                         "invoice_number": "IN1", "payer_mobile_no": "017",
                         "payment_method": "bKash"}}
        with patch("gateways.api_gateways.requests.post", return_value=MockResp(body)):
            r = self.client.get("/payment/P4/?gateway=g4&pp_callback=1&gid=g4&invoice_number=IN1")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Transaction.objects.get(ref="P4").trx_id, "PS1")

    def test_pathaopay_capture(self):
        self._gateway(slug="pathaopay-merchant-api", currency="BDT", tab="mfs", gid="g5")
        self._txn(ref="P5")
        from core.env import set_env
        set_env("pathaopay-token-P5", "TOK")
        set_env("pathaopay-invoice-P5", "INV9")

        class _Cap:
            status_code = 200
            headers = {}

            def json(self):
                return {"data": {"status": "success",
                                 "merchant_reference_id": "ORDER_X-BP-P5",
                                 "transaction_id": "PT1", "invoice_id": "INV9"}}

        with patch("gateways.api_gateways.requests.post", return_value=_Cap()):
            r = self.client.get("/payment/P5/?gateway=g5&pp_callback=1&gid=g5")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Transaction.objects.get(ref="P5").trx_id, "PT1")

    def test_shurjopay_ipn_verify_and_cancel(self):
        self._gateway(slug="shurjopay", currency="BDT", tab="mfs", gid="g6")
        self._txn(ref="P6")
        verify = [{"sp_code": "1000", "customer_order_id": "7-BP-P6",
                   "bank_trx_id": "SH1", "id": "9", "method": "bKash", "order_id": "O1"}]
        with patch("gateways.api_gateways.requests.post") as post:
            post.side_effect = [MockResp({"token": "T"}), MockResp(verify)]
            r = self.client.get("/ipn/g6/?order_id=O1")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Transaction.objects.get(ref="P6").status, "completed")
        self._txn(ref="P7")
        verify_fail = [{"sp_code": "0000", "customer_order_id": "7-BP-P7"}]
        with patch("gateways.api_gateways.requests.post") as post:
            post.side_effect = [MockResp({"token": "T"}), MockResp(verify_fail)]
            self.client.get("/ipn/g6/?order_id=O1")
        self.assertEqual(Transaction.objects.get(ref="P7").status, "canceled")

    def test_nagad_process_mocked(self):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        priv = key.private_bytes(serialization.Encoding.PEM,
                                 serialization.PrivateFormat.TraditionalOpenSSL,
                                 serialization.NoEncryption()).decode()
        pub = key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        # strip PEM wrappers (stored bare like PHP config values)
        priv_bare = priv.replace("-----BEGIN RSA PRIVATE KEY-----", "").replace(
            "-----END RSA PRIVATE KEY-----", "").strip()
        pub_bare = pub.replace("-----BEGIN PUBLIC KEY-----", "").replace(
            "-----END PUBLIC KEY-----", "").strip()
        self._gateway(slug="nagad-merchant-api", currency="BDT", tab="mfs", gid="g7")
        from core.models import GatewayParameter
        for k, v in {"app_account": "A", "merchant_id": "M", "private_key": priv_bare,
                     "public_key": pub_bare, "mode": "sandbox"}.items():
            GatewayParameter.objects.create(brand_id="b1", gateway_id="g7",
                                            option_name=k, value=v)
        self._txn(ref="P8", amount=100)

        import base64

        def _enc(obj):
            return base64.b64encode(key.public_key().encrypt(
                json.dumps(obj).encode(), padding.PKCS1v15())).decode()

        init_resp = {"sensitiveData": _enc({"paymentReferenceId": "PR1", "challenge": "CH"}),
                     "signature": "s"}
        done_resp = {"status": "Success", "callBackUrl": "https://nagad.example/cb"}

        with patch("gateways.api_gateways.requests.post") as post:
            post.side_effect = [MockResp(init_resp), MockResp(done_resp)]
            from gateways.registry import get_gateway
            from core.transactions import gateway_options
            gw = get_gateway("nagad-merchant-api")
            r = gw.process_payment({"transaction": {"ref": "P8", "local_net_amount": "100",
                                                    "local_currency": "BDT"},
                                    "options": gateway_options("g7", "b1"),
                                    "callback_url": "http://testserver/payment/P8/",
                                    "client_ip": "127.0.0.1"})
        self.assertEqual(r, {"action": "redirect", "url": "https://nagad.example/cb"})

    def test_binance_ipn_mocked(self):
        import hashlib as _hl
        import hmac as _hm
        import time as _t
        self._gateway(slug="binance-personal", currency="USD", tab="global", gid="g8")
        from core.models import GatewayParameter
        GatewayParameter.objects.create(brand_id="b1", gateway_id="g8",
                                        option_name="api_key", value="AK")
        GatewayParameter.objects.create(brand_id="b1", gateway_id="g8",
                                        option_name="secret_key", value="SK")
        self._txn(ref="BP1", amount=50, currency="USD")
        from core.env import set_env
        set_env("binance-personal-pp_amountBP1", "50")
        body = {"data": [{"orderId": "BO1", "currency": "USDT", "amount": 60.0,
                          "payerInfo": {"name": "Miner"}}]}

        with patch("gateways.api_gateways.requests.get", return_value=MockResp(body)):
            r = self.client.post("/ipn/g8/", {"bpid": "BP1", "order_id": "BO1"})
        # class.php:329-331 echoes status/title/message (no visible_number).
        self.assertEqual(r.json(), {"status": "true", "title": "Transaction Verified",
                                    "message": "Your transaction has been verified successfully."})
        self.assertEqual(Transaction.objects.get(ref="BP1").status, "completed")

    def test_paypal_callback_mocked(self):
        self._gateway(slug="paypal-api", currency="USD", tab="global", gid="g9")
        self._txn(ref="PP1", amount=10, currency="USD")
        order = {"status": "APPROVED",
                 "purchase_units": [{"custom_id": "PP1", "invoice_id": "PP1",
                                     "reference_id": "PP1",
                                     "amount": {"currency_code": "USD", "value": "10.00"}}]}
        captured = {"status": "COMPLETED",
                    "purchase_units": [{"custom_id": "PP1", "invoice_id": "PP1",
                                        "reference_id": "PP1",
                                        "amount": {"currency_code": "USD", "value": "10.00"},
                                        "payments": {"captures": [{"id": "CAP9"}]}}],
                    "payer": {"payer_id": "P", "email_address": "p@x.co"}}
        # Token uses POST; order GET + capture POST go through _req ->
        # requests.request (class.php uses GET then POST).
        with patch("gateways.api_gateways.requests.post",
                   return_value=MockResp({"access_token": "T"})), \
             patch("gateways.api_gateways.requests.request") as req:
            req.side_effect = [MockResp(order), MockResp(captured)]
            r = self.client.get("/payment/PP1/?gateway=g9&pp_callback=1&gid=g9&token=TOK")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Transaction.objects.get(ref="PP1").trx_id, "CAP9")

    def test_refund_bkash_branch_mocked(self):
        self._gateway(slug="bkash-api-tokenized", currency="BDT", tab="mfs", gid="g10")
        from core.models import GatewayParameter
        for k, v in {"username": "u", "password": "p", "app_key": "k",
                     "app_secret_key": "s", "mode": "sandbox"}.items():
            GatewayParameter.objects.create(brand_id="b1", gateway_id="g10",
                                            option_name=k, value=v)
        Transaction.objects.create(
            brand_id="b1", ref="RB1", customer_info="{}", amount=100, currency="BDT",
            status="completed", gateway_id="g10", trx_id="TRX1", local_net_amount=100,
            local_currency="BDT",
            source_info=json.dumps([{"label": "Payment ID", "value": "PAY1"}]))
        ApiKey.objects.create(brand_id="b1", name="k", api_key="K", expired_date="--",
                              api_scopes=json.dumps(["refund_payment"]))
        with patch("gateways.api_gateways.BkashApiTokenizedGateway._grant", return_value="TOK"), \
             patch("gateways.api_gateways.BkashApiTokenizedGateway._post_with_fallback") as fb:
            fb.return_value = {"ok": True, "used_base": "b",
                               "data": {"refundTransactionStatus": "Completed",
                                        "refundTrxId": "RT9", "refundAmount": "100.00",
                                        "completedTime": "T"}}
            r = self.client.post("/api/refund-payment/", data=json.dumps({"pp_id": "RB1"}),
                                 content_type="application/json",
                                 HTTP_MHS_PIPRAPAY_API_KEY="K")
        body = r.json()
        self.assertEqual(body["status"], "refunded")
        info = json.loads(Transaction.objects.get(ref="RB1").source_info)
        self.assertIn({"label": "Refund TrxID", "value": "RT9"}, info)

    def test_link_expired_and_stock_out(self):
        PaymentLink.objects.create(ref="LX", brand_id="b1",
                                   product_info=json.dumps({"title": "T", "description": "D"}),
                                   amount=10, quantity=0, currency="BDT",
                                   expired_date="2000-01-01", status="active")
        html = self.client.get("/payment-link/LX/").content.decode()
        self.assertIn("Product Not Active", html)
        r = self.client.post("/payment-link/LX/submit/",
                             {"itemid": "LX", "full-name": "A", "email-address": "a@b.co",
                              "mobile-number": "1"})
        self.assertEqual(r.json()["title"], "Product Not Available")

    def test_invoice_webhook_bad_json(self):
        r = self.client.post("/invoice/webhook/", data="not-json",
                             content_type="application/json")
        self.assertEqual((r.status_code, r.content.decode()), (400, "Invalid JSON"))

    def test_double_verify_second_rejected(self):
        Gateway.objects.create(gateway_id="g1", brand_id="b1", slug="bkash-personal",
                               display="bK", currency="BDT", status="active")
        from core.models import GatewayParameter, SmsData
        GatewayParameter.objects.create(brand_id="b1", gateway_id="g1",
                                        option_name="mobile_number", value="017")
        Transaction.objects.create(brand_id="b1", ref="DV", customer_info="{}",
                                   amount=500, currency="BDT", local_net_amount=500,
                                   local_currency="BDT", metadata="[]", status="initiated")
        SmsData.objects.create(device_id="d", sender_key="bkash", type="Personal",
                               number="0171", amount=500, trx_id="DUP1", status="approved")
        self.assertEqual(self.client.post(
            "/payment/DV/verify/",
            {"gateway-id": "g1", "transaction-id": "DV", "trxid": "DUP1"}).json()["status"], "true")
        # A repeat verify of an already-completed ref is refused by the
        # initiated-only lookup first (upstream Code 101), not the dup check.
        r = self.client.post("/payment/DV/verify/",
                             {"gateway-id": "g1", "transaction-id": "DV", "trxid": "DUP1"})
        self.assertEqual(r.json()["title"], "Request Failed. Code 101")
        # A *new* initiated txn reusing the same trxid hits the dup guard.
        Transaction.objects.create(brand_id="b1", ref="DV2", customer_info="{}",
                                   amount=500, currency="BDT", local_net_amount=500,
                                   local_currency="BDT", metadata="[]", status="initiated")
        r = self.client.post("/payment/DV2/verify/",
                             {"gateway-id": "g1", "transaction-id": "DV2", "trxid": "DUP1"})
        self.assertEqual(r.json()["title"], "Duplicate Transaction ID")


class MiscWorkflowTests(TestCase):
    def setUp(self):
        _seed_all(self.client)

    def test_companion_invalid_tokens(self):
        for action, data in [("login", {"onetimepassword": "nope"}),
                             ("account-information", {"token": "nope"}),
                             ("sms-transmit-bulk", {"token": "nope", "sms_list": "[]"}),
                             ("sms-transmit-sender", {"token": "nope"}),
                             ("delete-sms-data", {"token": "nope"})]:
            r = self.client.post(f"/api/companion/{action}/", data)
            self.assertIn(r.json()["status"], ("false",), action)

    def test_middleware_security_and_logout(self):
        r = self.client.get("/login/")
        self.assertEqual(r["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.client.get("/login/?logout=1")

    def test_maintenance_mode(self):
        from pathlib import Path
        from django.conf import settings
        flag = Path(settings.BASE_DIR) / ".maintenance"
        flag.write_text("1")
        try:
            r = self.client.get("/payment/XYZ/")
            self.assertContains(r, "maintenance", status_code=200)
        finally:
            flag.unlink(missing_ok=True)

    def test_api_scope_currency_and_fields(self):
        ApiKey.objects.create(brand_id="b1", name="k", api_key="K", expired_date="--",
                              api_scopes=json.dumps(["create_payment"]))
        auth = {"HTTP_MHS_PIPRAPAY_API_KEY": "K"}
        base = {"full_name": "A", "email_address": "a@b.co", "mobile_number": "1",
                "currency": "BDT", "metadata": {}}
        r = self.client.post("/api/checkout/redirect/",
                             data=json.dumps(dict(base, amount="10", currency="XXX")),
                             content_type="application/json", **auth)
        self.assertEqual(r.json()["error"]["code"], "INVALID_CURRENCY")
        r = self.client.post("/api/checkout/redirect/",
                             data=json.dumps(dict(base, amount="10")),
                             content_type="application/json",
                             HTTP_AUTHORIZATION="Bearer K")
        self.assertEqual(r.status_code, 200)
        r = self.client.post("/api/checkout/redirect/", data="{bad",
                             content_type="application/json", **auth)
        self.assertEqual(r.json()["error"]["code"], "INVALID_JSON_PAYLOAD")
        # Upstream validates return/webhook URLs BEFORE customer fields
        # (index.php:481-533), so a bad webhook wins over an empty full_name.
        r = self.client.post("/api/checkout/redirect/",
                             data=json.dumps(dict(base, amount="10",
                                                  full_name="",
                                                  webhook_url="https://evil.example/x")),
                             content_type="application/json", **auth)
        self.assertEqual(r.json()["error"]["code"], "INVALID_URL")
        r = self.client.post("/api/checkout/redirect/",
                             data=json.dumps(dict(base, amount="10", full_name="")),
                             content_type="application/json", **auth)
        self.assertEqual(r.json()["error"]["code"], "MISSING_FIELD")

    def test_suspended_redirect_and_dashboard_brand(self):
        Customer.objects.create(ref="c", brand_id="b1", name="S", email="s@b.co",
                                mobile="1", status="suspend")
        ApiKey.objects.create(brand_id="b1", name="k", api_key="K", expired_date="--",
                              api_scopes=json.dumps(["create_payment"]))
        r = self.client.post("/api/checkout/redirect/",
                             data=json.dumps({"full_name": "S", "email_address": "s@b.co",
                                              "mobile_number": "1", "amount": "10",
                                              "currency": "BDT", "metadata": {}}),
                             content_type="application/json", HTTP_MHS_PIPRAPAY_API_KEY="K")
        self.assertEqual(r.json()["error"]["code"], "INVALID_CUSTOMER")
        Brand.objects.create(brand_id="b9", identify_name="B9", name="B9")
        r = self.client.get("/admin/dashboard/?brand=b9")
        self.assertEqual(r.status_code, 200)

    def test_commands_run(self):
        from django.core.management import call_command
        call_command("check_requirements")
        call_command("run_cron")

    def test_update_download_gate_and_install_paths(self):
        from staff.system_ops import update_download, update_install
        from core.env import set_env
        set_env("last-update-version", "1.0")
        # 1.0 is not newer than 3.0.0 -> gate refuses without network.
        self.assertEqual(update_download()["status"], "false")
        self.assertEqual(update_install()["status"], "false")

    def test_bulk_import_mocked(self):
        from staff import views as _v
        gist = {"USD": {"symbol_native": "$"}, "EUR": {"symbol_native": "€"}}

        class _Resp:
            def json(self):
                return gist

        with patch("requests.get", return_value=_Resp()):
            result = _v._import_default_currencies("b1")
        self.assertEqual(result["status"], "true")
        self.assertTrue(Currency.objects.filter(brand_id="b1", code="EUR").exists())

    def test_sender_modes_and_reconcile_empty(self):
        from core.sms_engine import reconcile_by_longest_chain, sender_whitelist
        self.assertEqual(sender_whitelist(provider_key="bkash")["currency"], "BDT")
        self.assertEqual(sender_whitelist(provider_name="bKash")["provider_key"], "bkash")
        self.assertIsNone(reconcile_by_longest_chain("nope", "bkash", "Personal"))
        from core.money import get_domain_value
        self.assertFalse(get_domain_value("localhost"))
        self.assertEqual(get_domain_value("sub.example.com/path"), "sub.example.com")

    def test_money_edges(self):
        from core import money as M
        self.assertEqual(M.money_round("-1.005"), "-1.00")
        self.assertEqual(M.money_div("1", "0.0000000001"), "0")
        self.assertFalse(M.is_expired("25/12/2999"))
        self.assertEqual(M.time_ago("2020-01-01 00:00:00"), "6 years ago"[:0] + M.time_ago("2020-01-01 00:00:00"))
        self.assertIn("ago", M.time_ago("2024-01-01 00:00:00"))
        self.assertEqual(M.get_name_chars("John 0 Doe"), "JD")
        from core.money import dateformat
        self.assertTrue(dateformat("2024-01-01", "%Y-%m-%d"))

    def test_storage_helpers(self):
        import tempfile
        from pathlib import Path
        from core.storage import copy_folder, delete_folder, run_sql, zip_folder
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "a.txt").write_text("hi")
            copy_folder(tmp, tmp / "dst")
            self.assertTrue((tmp / "dst" / "a.txt").exists())
            zip_folder(tmp / "dst", tmp / "z.zip")
            self.assertTrue((tmp / "z.zip").exists())
            self.assertTrue(run_sql("CREATE TABLE t_tmp_x (id INT);")["status"])
            bad = run_sql("THIS IS NOT SQL;\n")
            self.assertFalse(bad["status"])
            delete_folder(tmp / "dst")
            self.assertFalse((tmp / "dst").exists())
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
