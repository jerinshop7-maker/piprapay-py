"""Workflow integration tests: staff CRUD, checkout flows, cron steps,
gateway callbacks, auth flows, reports, SMS/balance/currency ops."""
import json
from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.test import TestCase

from core.models import (Admin, ApiKey, BalanceVerification, Brand, Currency, Customer,
                         Device, Domain, Faq, Gateway, GatewayParameter, Invoice, InvoiceItem,
                         PaymentLink, PaymentLinkField, Permission, SmsData, Transaction,
                         WebhookLog)


def _brand(bid="b1"):
    return Brand.objects.create(brand_id=bid, identify_name=bid, name=bid)


def _admin(username="root", role="admin", a_id="a1"):
    return Admin.objects.create(a_id=a_id, full_name="R", username=username,
                                email=f"{username}@x.co", password=make_password("pw"),
                                role=role, twofa_secret="JBSWY3DPEHPK3PXP")


def _login_staff(client, brand="b1"):
    _brand(brand)
    Currency.objects.create(brand_id=brand, code="BDT", symbol="t", rate=1)
    _admin()
    Permission.objects.create(brand_id=brand, a_id="a1", permission="{}")
    client.post("/login/", {"username": "root", "password": "pw"})


class StaffCrudTests(TestCase):
    def setUp(self):
        _login_staff(self.client)

    def test_customer_create_suspend_delete(self):
        r = self.client.post("/admin/customers/",
                             {"brand_id": "b1", "name": "N", "email": "n@x.co", "mobile": "1"})
        self.assertEqual(r.status_code, 302)
        c = Customer.objects.get(email="n@x.co")
        self.client.post("/admin/customers/", {"suspend": c.id, "reason": "fraud"})
        c.refresh_from_db()
        self.assertEqual((c.status, c.suspend_reason), ("suspend", "fraud"))
        self.client.post("/admin/customers/", {"delete": c.id})
        self.assertFalse(Customer.objects.filter(email="n@x.co").exists())

    def test_invoice_create_and_pay(self):
        r = self.client.post("/admin/invoice/create/",
                             {"brand_id": "b1", "customer_name": "N", "customer_email": "n@x.co",
                              "customer_mobile": "1", "currency": "BDT", "due_date": "2027-01-01",
                              "shipping": "5", "status": "unpaid",
                              "item_description": ["Widget"], "item_amount": ["100"],
                              "item_quantity": ["2"], "item_discount": ["10"], "item_vat": ["5"]})
        self.assertEqual(r.status_code, 302)
        inv = Invoice.objects.get()
        self.assertEqual(InvoiceItem.objects.filter(invoice_id=inv.ref).count(), 1)
        # Payer pays the invoice -> transaction created with invoice metadata.
        r = self.client.post(f"/invoice/{inv.ref}/pay/", {"itemid": inv.ref})
        body = r.json()
        self.assertEqual(body["status"], "true")
        txn = Transaction.objects.get(ref=body["redirect"].rsplit("/", 1)[-1])
        self.assertEqual(json.loads(txn.metadata), {"invoice_id": inv.ref})
        self.assertEqual(txn.source, "invoice")

    def test_payment_link_create_submit_and_stock(self):
        r = self.client.post("/admin/payment-link/create/",
                             {"brand_id": "b1", "title": "T", "description": "D",
                              "amount": "50", "quantity": "2", "currency": "BDT",
                              "expired_date": "", "status": "active",
                              "items[n1][formType]": "text", "items[n1][fieldName]": "Note",
                              "items[n1][required]": "false"})
        self.assertEqual(r.status_code, 302)
        link = PaymentLink.objects.get()
        self.assertEqual(link.expired_date, "--")
        self.assertEqual(PaymentLinkField.objects.filter(paymentLinkID=link.ref).count(), 1)
        r = self.client.post(f"/payment-link/{link.ref}/submit/",
                             {"itemid": link.ref, "full-name": "A",
                              "email-address": "a@b.co", "mobile-number": "1",
                              "note": "hi"})
        self.assertEqual(r.json()["status"], "true")
        link.refresh_from_db()
        self.assertEqual(link.quantity, 1)
        txn = Transaction.objects.get(source="payment-link")
        self.assertEqual(json.loads(txn.source_info), [{"label": "Note", "value": "hi"}])

    def test_link_default_submit(self):
        r = self.client.post("/payment-link/default/b1/submit/",
                             {"itemid": "b1", "full-name": "A", "email-address": "a@b.co",
                              "mobile-number": "1", "amount": "25"})
        self.assertEqual(r.json()["status"], "true")
        txn = Transaction.objects.get(source="payment-link-default")
        self.assertEqual(str(txn.amount), "25.00000000")

    def test_gateway_create_and_toggle(self):
        r = self.client.post("/admin/gateways/create/",
                             {"slug": "bkash-personal", "brand_id": "b1", "name": "bK",
                              "display": "bKash", "currency": "BDT", "tab": "mfs",
                              "status": "active", "min_allow": "10", "max_allow": "1000",
                              "fixed_discount": "0", "percentage_discount": "0",
                              "fixed_charge": "1", "percentage_charge": "0",
                              "mobile_number": "017", "pending_payment": "disable"})
        self.assertEqual(r.status_code, 302)
        gw = Gateway.objects.get(slug="bkash-personal")
        self.assertEqual(GatewayParameter.objects.get(gateway_id=gw.gateway_id,
                                                      option_name="mobile_number").value, "017")
        self.assertEqual(gw.tab, "mfs")

    def test_staff_create_edit_matrix(self):
        self.client.post("/admin/staff/create/",
                         {"full_name": "S", "username": "s", "email": "s@x.co",
                          "password": "pw123456", "status": "active", "brands": ["b1"]})
        s = Admin.objects.get(username="s")
        self.assertEqual(s.role, "staff")
        self.assertTrue(Permission.objects.filter(a_id=s.a_id, brand_id="b1").exists())
        row = Permission.objects.get(a_id=s.a_id)
        self.client.post(f"/admin/staff/{s.a_id}/permissions/",
                         {"permission_id": row.id, "res_transaction_approve": "on"})
        row.refresh_from_db()
        self.assertTrue(json.loads(row.permission)["resources"]["transaction"]["approve"])

    def test_reports_and_stats_json(self):
        Transaction.objects.create(brand_id="b1", ref="R1", customer_info="{}",
                                   amount=100, currency="BDT", status="completed")
        r = self.client.get("/admin/reports/?preset=today")
        self.assertEqual(r.status_code, 200)
        r = self.client.get("/admin/reports/transaction-stats/?preset=today")
        body = r.json()
        self.assertIn("labels", body)
        self.assertIn("series", body)
        r = self.client.get("/admin/reports/gateway-stats/")
        self.assertIn("gateways", r.json())

    def test_sms_auto_edit_bulk(self):
        msg = ("You have received Tk 500.00 from 01700000000. Fee Tk 0.00. "
               "Balance Tk 10,500.00. TrxID SMS1AAAAA at 2024/01/01 12:00")
        self.client.post("/admin/sms-data/",
                         {"entry_type": "automatic", "sender_key": "bkash",
                          "message": msg, "status": "approved"})
        sms = SmsData.objects.get(trx_id="SMS1AAAAA")
        self.assertEqual((sms.type, str(sms.amount)), ("Personal", "500.00000000"))
        self.client.post("/admin/sms-data/",
                         {"edit_id": sms.id, "sender_key": "bkash", "status": "used",
                          "message": msg})
        sms.refresh_from_db()
        self.assertEqual(sms.status, "used")

    def test_balance_crud_guards(self):
        Device.objects.create(d_id="x", device_id="d1", otp="o", status="used")
        self.client.post("/admin/devices/balance-verification/",
                         {"device_id": "d1", "sender_key": "bkash", "type": "Personal",
                          "current_balance": "100", "simslot": "Any", "status": "active"})
        rule = BalanceVerification.objects.get()
        # Duplicate rejected.
        self.client.post("/admin/devices/balance-verification/",
                         {"device_id": "d1", "sender_key": "bkash", "type": "Personal",
                          "current_balance": "5", "simslot": "Any", "status": "active"})
        self.assertEqual(BalanceVerification.objects.count(), 1)
        # Quick update + delete.
        self.client.post("/admin/devices/balance-verification/", {"iupdate": rule.id,
                                                                   "current_balance": "200"})
        rule.refresh_from_db()
        self.assertEqual(str(rule.current_balance), "200.00000000")
        self.client.post("/admin/devices/balance-verification/", {"delete": rule.id})
        self.assertEqual(BalanceVerification.objects.count(), 0)

    def test_currency_sync_mocked(self):
        from core.models import Currency as _C
        _C.objects.create(brand_id="b1", code="USD", symbol="$", rate=0)
        row = _C.objects.get(brand_id="b1", code="USD")
        fx = {"bdt": {"usd": 0.0085, "bdt": 1.0}}

        class _Resp:
            def json(self):
                return fx

        with patch("requests.get", return_value=_Resp()):
            self.client.post("/admin/brand-setting/currency/", {"sync": row.id})
        row.refresh_from_db()
        self.assertNotEqual(str(row.rate), "0.00000000")

    def test_my_account_gates_and_2fa(self):
        # Profile change without current password is rejected.
        r = self.client.post("/admin/my-account/", {"full_name": "New"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Admin.objects.get(username="root").full_name, "R")
        r = self.client.post("/admin/my-account/",
                             {"full_name": "New", "current_password": "pw"})
        self.assertEqual(Admin.objects.get(username="root").full_name, "New")

    def test_addon_upload_rejected_and_system_update_check(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        r = self.client.post("/admin/addons/",
                             {"upload": "1",
                              "addon_zip": SimpleUploadedFile("x.zip", b"not-a-zip")})
        self.assertEqual(r.status_code, 302)
        manifest = {"channels": {"stable": {"latest_version_name": "v9",
                                            "latest_version_code": "9",
                                            "versions": [{"version_code": "9",
                                                          "checksum": "abc"}]}}}

        class _Resp:
            def json(self):
                return manifest

        with patch("requests.get", return_value=_Resp()):
            r = self.client.post("/admin/system-settings/", {"op": "update-check"})
        self.assertEqual(r.status_code, 302)
        from core.env import get_env
        self.assertEqual(get_env("last-update-version"), "9")

    def test_import_bad_zip(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        r = self.client.post("/admin/system-settings/",
                             {"op": "import",
                              "module_zip": SimpleUploadedFile("m.zip", b"nope")})
        self.assertEqual(r.status_code, 302)


class AuthFlowTests(TestCase):
    def setUp(self):
        _brand("b1")
        Currency.objects.create(brand_id="b1", code="BDT", symbol="t", rate=1)
        _admin()
        Permission.objects.create(brand_id="b1", a_id="a1", permission="{}")

    def test_forgot_resets_and_temp_login(self):
        r = self.client.post("/forgot/", {"email-address": "root@x.co"})
        self.assertEqual(r.status_code, 302)
        admin = Admin.objects.get(username="root")
        self.assertEqual(admin.reset_limit, "2")
        # Temp password hash stored; exhaustion message after 3 uses.
        for _ in range(2):
            self.client.post("/forgot/", {"email-address": "root@x.co"})
        r = self.client.post("/forgot/", {"email-address": "root@x.co"})
        self.assertContains(r, "maximum number", status_code=200)

    def test_2fa_wrong_code_rejected(self):
        admin = Admin.objects.get(username="root")
        admin.twofa_status = "enable"
        admin.save()
        self.client.post("/login/", {"username": "root", "password": "pw"})
        r = self.client.post("/2fa/", {"code": "000000"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/admin/dashboard/").status_code, 302)

    def test_2fa_correct_code_accepts(self):
        from unittest.mock import patch as _patch
        from core.totp import totp_now
        admin = Admin.objects.get(username="root")
        admin.twofa_status = "enable"
        admin.twofa_secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        admin.save()
        self.client.post("/login/", {"username": "root", "password": "pw"})
        with _patch("core.totp.time.time", return_value=1111111109):
            r = self.client.post("/2fa/", {"code": totp_now(admin.twofa_secret)})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.get("/admin/dashboard/").status_code, 200)

    def test_totp_rfc_vector(self):
        from unittest.mock import patch as _patch
        from core.totp import totp_now, verify_totp
        # RFC 6238 Appendix B, SHA1 secret "12345678901234567890".
        with _patch("core.totp.time.time", return_value=1111111109):
            self.assertEqual(totp_now("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"), "081804")
            self.assertTrue(verify_totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", "081804"))
            self.assertFalse(verify_totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", "000000"))

    def test_set_brand_invalid(self):
        self.client.post("/login/", {"username": "root", "password": "pw"})
        r = self.client.post("/set-brand/", {"brand_id": "nope"})
        self.assertEqual(r.status_code, 302)
        self.assertNotEqual(self.client.session.get("pp_brand_id"), "nope")


class CronWorkflowTests(TestCase):
    def setUp(self):
        _brand("b1")
        Currency.objects.create(brand_id="b1", code="BDT", symbol="t", rate=1)
        Gateway.objects.create(gateway_id="g1", brand_id="b1", slug="bkash-personal",
                               display="bK", currency="BDT", status="active")
        from core.models import GatewayParameter
        GatewayParameter.objects.create(brand_id="b1", gateway_id="g1",
                                        option_name="mobile_number", value="017")
        Transaction.objects.create(brand_id="b1", ref="P1", customer_info="{}",
                                   amount=500, currency="BDT", local_net_amount=500,
                                   local_currency="BDT", status="pending",
                                   sender_key="bkash", sender_type="Personal", trx_id="CRON1",
                                   webhook_url="--")
        SmsData.objects.create(device_id="d", sender_key="bkash", type="Personal",
                               number="0171", amount=500, trx_id="CRON1", status="approved")

    def test_verify_pending_completes_and_consumes_sms(self):
        from cron_app.tasks import verify_pending
        done = verify_pending()
        self.assertEqual(len(done), 1)
        self.assertEqual(Transaction.objects.get(ref="P1").status, "completed")
        self.assertEqual(SmsData.objects.get(trx_id="CRON1").status, "used")

    def test_lock_contention_returns_shape(self):
        from pathlib import Path
        from django.conf import settings
        from cron_app.tasks import run_cron
        lock = Path(settings.BASE_DIR) / "media" / "storage" / "cron.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("x")
        try:
            self.assertEqual(run_cron(), {"status": "true", "message": "Cron run executed."})
        finally:
            lock.unlink(missing_ok=True)

    def test_retry_exhaustion_cancels(self):
        from cron_app.tasks import retry_webhooks
        from core.env import set_env
        set_env("geneal-application-settings-webhook_attempts_limit", "1")
        WebhookLog.objects.create(ref="w", brand_id="b1", payload="{}",
                                  url="http://127.0.0.1:9/hook")
        with patch("core.webhooks.requests.post", side_effect=Exception("down")):
            retry_webhooks()
        row = WebhookLog.objects.get(ref="w")
        self.assertEqual((row.status, row.attempts), ("canceled", 1))

    def test_webhook_failure_queues_log(self):
        Transaction.objects.create(brand_id="b1", ref="P2", customer_info="{}",
                                   amount=10, currency="BDT", status="initiated",
                                   gateway_id="g1", webhook_url="http://127.0.0.1:9/h")
        from core.transactions import set_transaction_status
        with patch("core.webhooks.requests.post", side_effect=Exception("down")):
            self.assertTrue(set_transaction_status("P2", "completed", "g1", "T9", []))
        self.assertTrue(WebhookLog.objects.filter(url="http://127.0.0.1:9/h").exists())

    def test_reconcile_bfs_chain(self):
        from core.sms_engine import reconcile_by_longest_chain
        from core.models import BalanceVerification
        BalanceVerification.objects.create(device_id="d", sender_key="bkash",
                                           type="Personal", current_balance="1000",
                                           simslot="Any", status="active")
        SmsData.objects.create(device_id="d", source="app", sender_key="bkash",
                               type="Personal", number="1", amount=200, balance="1200",
                               trx_id="A1", status="approved")
        SmsData.objects.create(device_id="d", source="app", sender_key="bkash",
                               type="Personal", number="1", amount=300, balance="1500",
                               trx_id="A2", status="awaiting-review")
        reconcile_by_longest_chain("d", "bkash", "Personal")
        rule = BalanceVerification.objects.get(device_id="d")
        self.assertEqual(str(rule.current_balance), "1500.00000000")


class HooksTests(TestCase):
    def test_ordering_and_crash_isolation(self):
        from core import hooks
        seen = []
        hooks.add_action("t.evt", lambda: seen.append(1), priority=20)
        hooks.add_action("t.evt", lambda: 1 / 0, priority=10)
        hooks.add_action("t.evt", lambda: seen.append(0), priority=5)
        try:
            hooks.do_action("t.evt")
            self.assertEqual(seen, [0, 1])
            self.assertEqual(hooks.apply_filters("t.f", 2), 2)
        finally:
            hooks._actions.pop("t.evt", None)
            hooks._filters.pop("t.f", None)

    def test_set_status_guard_and_hook(self):
        _brand("b1")
        Currency.objects.create(brand_id="b1", code="BDT", symbol="t", rate=1)
        from core.models import Gateway
        Gateway.objects.create(gateway_id="g1", brand_id="b1", slug="bkash-personal",
                               currency="BDT", status="active")
        Transaction.objects.create(brand_id="b1", ref="T1", customer_info="{}",
                                   amount=10, currency="BDT", status="pending")
        from core import hooks
        from core.transactions import set_transaction_status
        seen = []
        hooks.add_action("transactions.updated", lambda rows: seen.extend(rows))
        try:
            self.assertFalse(set_transaction_status("T1", "completed", "g1", "X"))
            Transaction.objects.create(brand_id="b1", ref="T2", customer_info="{}",
                                       amount=10, currency="BDT", status="initiated")
            self.assertTrue(set_transaction_status("T2", "completed", "g1", "X"))
            self.assertEqual(len(seen), 1)
        finally:
            hooks._actions.pop("transactions.updated", None)


class FailureInjectionTests(TestCase):
    """Provider-side failure must never complete a transaction or emit a webhook."""

    def setUp(self):
        _login_staff(self.client)
        Gateway.objects.create(gateway_id="g1", brand_id="b1", slug="stripe",
                               currency="USD", tab="global", status="active")

    def _txn(self, ref="P1"):
        return Transaction.objects.create(
            brand_id="b1", ref=ref, customer_info=json.dumps({"name": "N"}),
            amount=100, currency="USD", metadata="[]", status="initiated")

    def test_provider_timeout_leaves_initiated(self):
        import requests as _rq
        self._txn()
        with patch("gateways.api_gateways.requests.get", side_effect=_rq.Timeout("t")):
            self.client.get("/payment/P1/?gateway=g1&pp_callback=1&gid=g1&session_id=cs_1")
        self.assertEqual(Transaction.objects.get(ref="P1").status, "initiated")
        self.assertFalse(WebhookLog.objects.exists())

    def test_malformed_provider_response_leaves_initiated(self):
        class _Bad:
            status_code = 200
            headers = {}

            def json(self):
                raise ValueError("no json")

        self._txn(ref="P2")
        with patch("gateways.api_gateways.requests.get", return_value=_Bad()):
            self.client.get("/payment/P2/?gateway=g1&pp_callback=1&gid=g1&session_id=cs_1")
        txn = Transaction.objects.get(ref="P2")
        self.assertEqual(txn.status, "initiated")
        self.assertEqual(txn.trx_id, "--")


class ProductionSettingsTests(TestCase):
    def test_secure_mode_rejects_dev_secrets(self):
        from django.core.exceptions import ImproperlyConfigured
        from config.settings import validate_production_settings, DEFAULT_DEV_SECRET_KEY
        # dev mode never raises
        validate_production_settings(False, DEFAULT_DEV_SECRET_KEY, ["*"])
        with self.assertRaises(ImproperlyConfigured):
            validate_production_settings(True, DEFAULT_DEV_SECRET_KEY, ["example.com"])
        with self.assertRaises(ImproperlyConfigured):
            validate_production_settings(True, "real-key", ["*"])
        validate_production_settings(True, "real-key", ["example.com"])


class MockResp:
    def __init__(self, payload, status=200, headers=None):
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class ProviderCallbackTests(TestCase):
    """Mocked success/failure branches for API gateways not otherwise covered
    (SSLCommerz, OxaPay, NOWPayments). No live provider is contacted."""

    def setUp(self):
        _brand("b1")
        _admin()  # satisfy the install gate (redirects to /install/ when no Admin)
        Currency.objects.create(brand_id="b1", code="BDT", symbol="t", rate=1)

    def _gw(self, slug, currency="BDT", tab="mfs", gid="g1"):
        return Gateway.objects.create(gateway_id=gid, brand_id="b1", slug=slug,
                                      currency=currency, tab=tab, status="active")

    def _txn(self, ref="P1", currency="BDT", amount=100):
        return Transaction.objects.create(
            brand_id="b1", ref=ref, customer_info=json.dumps({"name": "N"}),
            amount=amount, currency=currency, metadata="[]", status="initiated")

    def test_sslcommerz_callback_success(self):
        self._gw("sslcommerz")
        self._txn()
        validator = {"APIConnect": "DONE", "no_of_trans_found": 1, "element": [{
            "status": "VALID", "value_a": "P1", "tran_id": "T1", "val_id": "V1",
            "bank_gw": "BK", "bank_tran_id": "BT1"}]}
        with patch("gateways.api_gateways.requests.get", return_value=MockResp(validator)):
            r = self.client.post("/payment/P1/?gateway=g1&pp_callback=1&gid=g1",
                                 {"tran_id": "T1"})
        self.assertEqual(r.status_code, 302)
        txn = Transaction.objects.get(ref="P1")
        self.assertEqual((txn.status, txn.trx_id), ("completed", "T1"))

    def test_sslcommerz_bad_validator_leaves_initiated(self):
        self._gw("sslcommerz")
        self._txn(ref="P2")
        validator = {"APIConnect": "DONE", "no_of_trans_found": 1,
                     "element": [{"status": "INVALID", "value_a": "P2"}]}
        with patch("gateways.api_gateways.requests.get", return_value=MockResp(validator)):
            self.client.post("/payment/P2/?gateway=g1&pp_callback=1&gid=g1", {"tran_id": "T"})
        self.assertEqual(Transaction.objects.get(ref="P2").status, "initiated")

    def test_oxapay_callback_success(self):
        from core.env import set_env
        self._gw("oxapay", currency="USD", tab="global")
        self._txn(currency="USD")
        set_env("oxapay-gateway-pp_P1", "TRK1")
        body = {"data": {"status": "paid", "amount": "100", "order_id": "X-BP-P1"}}
        with patch("gateways.api_gateways.requests.get", return_value=MockResp(body)):
            r = self.client.get("/payment/P1/?gateway=g1&pp_callback=1&gid=g1")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Transaction.objects.get(ref="P1").status, "completed")

    def test_oxapay_amount_mismatch_leaves_initiated(self):
        from core.env import set_env
        self._gw("oxapay", currency="USD", tab="global")
        self._txn(ref="P3", currency="USD")
        set_env("oxapay-gateway-pp_P3", "TRK3")
        body = {"data": {"status": "paid", "amount": "1", "order_id": "X-BP-P3"}}
        with patch("gateways.api_gateways.requests.get", return_value=MockResp(body)):
            self.client.get("/payment/P3/?gateway=g1&pp_callback=1&gid=g1")
        self.assertEqual(Transaction.objects.get(ref="P3").status, "initiated")

    def test_nowpayments_callback_success(self):
        from core.env import set_env
        self._gw("nowpayments", currency="USD", tab="global")
        self._txn(currency="USD")
        set_env("nowpayments-invoice-P1", "INV1")
        with patch("gateways.api_gateways.requests.request",
                   return_value=MockResp({"payment_status": "finished", "id": "INV1"})):
            r = self.client.get("/payment/P1/?gateway=g1&pp_callback=1&gid=g1")
        self.assertEqual(r.status_code, 302)
        txn = Transaction.objects.get(ref="P1")
        self.assertEqual((txn.status, txn.trx_id), ("completed", "INV1"))

    def test_nowpayments_cancel_stays_initiated(self):
        from core.env import set_env
        self._gw("nowpayments", currency="USD", tab="global")
        self._txn(currency="USD")
        set_env("nowpayments-invoice-P1", "INV1")
        self.client.get("/payment/P1/?gateway=g1&pp_callback=1&gid=g1&pp_status=cancel")
        self.assertEqual(Transaction.objects.get(ref="P1").status, "initiated")
