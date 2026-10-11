"""Second-wave parity tests: companion pairing, numeric juggling, dashboard
exclusion, API root, brand guards, permission grants, cron shape, IPN context."""
import json

from django.contrib.auth.hashers import make_password
from django.test import TestCase

from core import money as M
from core.models import (Admin, Brand, Currency, Device, Gateway, Invoice, Permission,
                         SmsData, Transaction)
from core.permissions import permission_schema


class InstallGateTests(TestCase):
    def test_empty_db_redirects_to_installer(self):
        r = self.client.get("/login/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/install/")
        r = self.client.get("/install/")
        self.assertEqual(r.status_code, 200)
        r = self.client.post("/install/admin/", {"full_name": "A", "username": "admin",
                                                 "email": "a@b.co", "password": "pw123456",
                                                 "password_confirm": "pw123456"})
        self.assertContains(r, "Installation Complete", status_code=200)
        self.assertTrue(Admin.objects.filter(username="admin").exists())
        self.assertTrue(Brand.objects.exists())
        r = self.client.get("/login/")
        self.assertEqual(r.status_code, 200)

    def test_legacy_page_param(self):
        Admin.objects.create(a_id="a", full_name="A", username="u", email="u@x.co",
                             password=make_password("pw"), role="admin")
        r = self.client.get("/?page=login")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/login/")
        r = self.client.get("/?page=..%2Fsecret")
        self.assertEqual(r.status_code, 302)


class NumericJugglingTests(TestCase):
    def test_num_eq(self):
        self.assertTrue(M.num_eq("100.50000000", "100.50"))
        self.assertTrue(M.num_eq("0", "0.00000000"))
        self.assertFalse(M.num_eq("100.51", "100.50"))


class CompanionFlowTests(TestCase):
    def setUp(self):
        Brand.objects.create(brand_id="b1", identify_name="B", name="B")
        self.admin = Admin.objects.create(a_id="a1", full_name="R", username="root",
                                          email="r@x.com", password=make_password("pw"),
                                          role="admin", status="active")
        Permission.objects.create(brand_id="b1", a_id="a1", permission="{}")

    def _login(self):
        self.client.post("/login/", {"username": "root", "password": "pw"})

    def test_pairing_uses_browser_cookie(self):
        self._login()
        r = self.client.post("/admin/devices/connect-info/")
        self.assertEqual(r.json()["status"], "true")
        otp = r.json()["otp"]
        dev = Device.objects.get(otp=otp)
        # d_id must equal the browser_log cookie so account-information resolves.
        from core.models import BrowserLog
        self.assertTrue(BrowserLog.objects.filter(a_id="a1", cookie=dev.d_id).exists())
        # Pair via companion login.
        r = self.client.post("/api/companion/login/",
                             {"onetimepassword": otp, "name": "Pixel", "model": "X"})
        self.assertEqual(r.json()["status"], "true")
        token = r.json()["token"]
        dev.refresh_from_db()
        self.assertEqual(dev.status, "used")
        # Account info resolves through d_id -> browser_log -> admin.
        r = self.client.post("/api/companion/account-information/", {"token": token})
        self.assertEqual(r.json()["status"], "true")
        self.assertEqual(r.json()["email"], "r@x.com")
        # Sender list + delete endpoints honour the token.
        r = self.client.post("/api/companion/sms-transmit-sender/", {"token": token})
        self.assertEqual(r.json()["status"], "true")
        self.assertIn("bkash", r.json()["senders"])
        r = self.client.post("/api/companion/delete-sms-data/", {"token": token, "error": "yes"})
        self.assertEqual(r.json()["status"], "true")

    def test_bulk_sms_balance_gating(self):
        self._login()
        otp = self.client.post("/admin/devices/connect-info/").json()["otp"]
        token = self.client.post("/api/companion/login/", {"onetimepassword": otp}).json()["token"]
        dev = Device.objects.get(otp=token)
        from core.models import BalanceVerification
        BalanceVerification.objects.create(device_id=dev.device_id, sender_key="bkash",
                                           type="Personal", current_balance="10000.00",
                                           simslot="Any", status="active")
        msg = ("You have received Tk 500.00 from 01700000000. Fee Tk 0.00. "
               "Balance Tk 10,500.00. TrxID CMP1AAAAA at 2024/01/01 12:00")
        r = self.client.post("/api/companion/sms-transmit-bulk/",
                             {"token": token, "sms_list": json.dumps(
                                 [{"sender": "bkash", "message": msg, "simSlot": "1"}])})
        self.assertEqual(r.json()["status"], "true")
        sms = SmsData.objects.get(trx_id="CMP1AAAAA")
        # "10500.00000000" == "10500.00" numerically -> approved + balance bumped.
        self.assertEqual(sms.status, "approved")
        dev.refresh_from_db()
        rule = BalanceVerification.objects.get(device_id=dev.device_id)
        self.assertTrue(M.num_eq(str(rule.current_balance), "10500"))
        # Unknown sender -> error row, no crash.
        r = self.client.post("/api/companion/sms-transmit-bulk/",
                             {"token": token, "sms_list": json.dumps(
                                 [{"sender": "nosuch", "message": "hi", "simSlot": "1"}])})
        self.assertEqual(r.json()["status"], "false")
        self.assertTrue(SmsData.objects.filter(status="error").exists())


class DashboardExclusionTests(TestCase):
    def test_initiated_excluded_from_total(self):
        Brand.objects.create(brand_id="b1", identify_name="B", name="B")
        Admin.objects.create(a_id="a1", full_name="R", username="root", email="r@x.com",
                             password=make_password("pw"), role="admin")
        Permission.objects.create(brand_id="b1", a_id="a1", permission="{}")
        Transaction.objects.create(brand_id="b1", ref="I1", customer_info="{}", amount=10,
                                   currency="BDT", status="initiated")
        Transaction.objects.create(brand_id="b1", ref="C1", customer_info="{}", amount=10,
                                   currency="BDT", status="completed")
        self.client.post("/login/", {"username": "root", "password": "pw"})
        r = self.client.get("/admin/dashboard/")
        self.assertContains(r, "Transactions: 1")


class MiscEndpointTests(TestCase):
    def test_api_root_json(self):
        Admin.objects.create(a_id="adm", full_name="A", username="installer",
                             email="i@x.co", password=make_password("pw"), role="admin")
        r = self.client.post("/api/", data=json.dumps({}), content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "INVALID_JSON_PAYLOAD")

    def test_brand_default_guard(self):
        Brand.objects.create(brand_id="b1", identify_name="Default", name="D")
        Brand.objects.create(brand_id="b2", identify_name="Second", name="S")
        Admin.objects.create(a_id="a1", full_name="R", username="root", email="r@x.com",
                             password=make_password("pw"), role="admin")
        Permission.objects.create(brand_id="b1", a_id="a1",
                                  permission=json.dumps(permission_schema()))
        self.client.post("/login/", {"username": "root", "password": "pw"})
        first = Brand.objects.order_by("id").first()
        r = self.client.post(f"/admin/brands/{first.brand_id}/", {"identify_name": "Renamed"})
        first.refresh_from_db()
        self.assertEqual(first.identify_name, "Default")

    def test_permission_grant_pages(self):
        Brand.objects.create(brand_id="b1", identify_name="B", name="B")
        Admin.objects.create(a_id="a1", full_name="R", username="root", email="r@x.com",
                             password=make_password("pw"), role="admin")
        staff = Admin.objects.create(a_id="s1", full_name="S", username="staff", email="s@x.com",
                                     password=make_password("pw"), role="staff")
        Permission.objects.create(brand_id="b1", a_id="a1", permission="{}")
        Permission.objects.create(brand_id="b1", a_id="s1", permission="{}")
        self.client.post("/login/", {"username": "root", "password": "pw"})
        r = self.client.get("/admin/staff/s1/brands/")
        self.assertEqual(r.status_code, 200)
        row = Permission.objects.filter(a_id="s1").first()
        r = self.client.get(f"/admin/staff/s1/permissions/?permission={row.id}")
        self.assertEqual(r.status_code, 200)

    def test_cron_shape(self):
        from cron_app.tasks import run_cron
        result = run_cron()
        self.assertEqual(result, {"status": "true", "message": "Cron run executed."})

    def test_invoice_webhook_marks_paid(self):
        from core.transactions import handle_invoice_webhook
        Brand.objects.create(brand_id="b1", identify_name="B", name="B")
        Invoice.objects.create(ref="INV9", brand_id="b1", customer_info="{}",
                               currency="BDT", status="unpaid")
        Transaction.objects.create(brand_id="b1", ref="TX9", customer_info="{}",
                                   amount=50, currency="BDT", status="completed",
                                   metadata=json.dumps({"invoice_id": "INV9"}))
        code, body = handle_invoice_webhook({"pp_id": "TX9"})
        self.assertEqual((code, body), (200, "OK"))
        self.assertEqual(Invoice.objects.get(ref="INV9").status, "paid")
        code, body = handle_invoice_webhook(None)
        self.assertEqual(code, 400)
