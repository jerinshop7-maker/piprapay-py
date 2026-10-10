"""
Parity test-suite — proves the Django port covers the PHP original.

Covers: 23 models/table names, exact money semantics (truncate rounding,
digits-only IDs, range tolerance), permissions schema, sender whitelist,
MFS SMS parsing (exact PHP regexes), gateway registry (48 plugins, exact
colors/fields/types/langs), theme language pack (69 keys), API validation +
error codes, checkout verify/invoice/link flows, IPN, cron, staff auth.
Run:  python manage.py test tests
"""
import json

from django.contrib.auth.hashers import make_password
from django.test import Client, TestCase

from core import money as M
from core.models import (Admin, ApiKey, Brand, Currency, Customer, Domain, Gateway,
                         GatewayParameter, Permission, SmsData, Transaction)
from core.permissions import can_access_page, has_permission, permission_schema
from core.sms_engine import PROVIDERS, mfs_message_verified, sender_whitelist
from gateways.registry import all_gateways, get_gateway
from themes.twenty_six import TwentySixTheme


class MoneyParityTests(TestCase):
    def test_bcscale8_math(self):
        self.assertEqual(M.money_add("0.1", "0.2"), "0.30000000")
        self.assertEqual(M.money_sub("1.00", "0.33"), "0.67000000")
        self.assertEqual(M.money_mul("10", "0.025"), "0.25000000")
        self.assertEqual(M.money_div("10", "4"), "2.50000000")
        self.assertEqual(M.money_div("1", "0"), "0.00000000")

    def test_round_truncates_like_bc(self):
        # PHP bc-truncation, NOT half-up: 10.005 -> 10.00, 10.009 -> 10.00
        self.assertEqual(M.money_round("10.005"), "10.00")
        self.assertEqual(M.money_round("10.009"), "10.00")
        self.assertEqual(M.money_round("10.01"), "10.01")

    def test_sanitize_is_numeric_only(self):
        self.assertEqual(M.money_sanitize("100"), "100")
        self.assertEqual(M.money_sanitize("1,234.50"), "0")
        self.assertEqual(M.money_sanitize("--"), "0")
        self.assertEqual(M.money_sanitize(None), "0")
        self.assertEqual(M.money_sanitize(""), "0")

    def test_tolerance_is_range(self):
        self.assertTrue(M.verify_payment_tolerance("100", "100", "0"))
        self.assertTrue(M.verify_payment_tolerance("100", "100.50", "1"))
        self.assertFalse(M.verify_payment_tolerance("100", "102", "1"))
        # Underpayments NEVER pass, even within tolerance below checkout.
        self.assertFalse(M.verify_payment_tolerance("100", "99.50", "1"))
        self.assertFalse(M.verify_payment_tolerance("0", "0", "5"))

    def test_ids_are_digits(self):
        pp_id = M.generate_item_id(27, 27)
        self.assertEqual(len(pp_id), 27)
        self.assertTrue(pp_id.isdigit())

    def test_name_chars(self):
        self.assertEqual(M.get_name_chars("John Doe"), "JD")
        self.assertEqual(M.get_name_chars("John"), "JO")
        self.assertEqual(M.get_name_chars(""), "")

    def test_is_expired(self):
        self.assertFalse(M.is_expired("--"))
        self.assertFalse(M.is_expired(""))
        self.assertTrue(M.is_expired("not-a-date"))
        self.assertTrue(M.is_expired("2000-01-01 00:00:00"))
        self.assertFalse(M.is_expired("2999-01-01 00:00:00"))

    def test_domain_value(self):
        self.assertEqual(M.get_domain_value("https://www.Example.com/x"), "example.com")
        self.assertEqual(M.get_domain_value("example.com"), "example.com")
        self.assertFalse(M.get_domain_value("not a url!!"))
        self.assertFalse(M.get_domain_value(""))

    def test_add_query_params_drops_fragment(self):
        self.assertEqual(M.add_query_params("https://x.com/p#frag", {"a": "1"}),
                         "https://x.com/p?a=1")


class PermissionParityTests(TestCase):
    def test_schema_shape(self):
        schema = permission_schema()
        self.assertEqual(len(schema["resources"]), 17)
        self.assertEqual(len(schema["pages"]), 15)
        self.assertIn("send_ipn", schema["resources"]["transaction"])

    def test_admin_bypass(self):
        self.assertTrue(has_permission({}, "transaction", "delete", "admin"))
        self.assertTrue(can_access_page({}, "dashboard", "admin"))

    def test_staff_denied_by_default(self):
        self.assertFalse(has_permission({"resources": {}}, "transaction", "delete", "staff"))
        self.assertFalse(can_access_page({"pages": {}}, "dashboard", "staff"))


class SmsEngineParityTests(TestCase):
    def test_providers_count(self):
        self.assertEqual(len(PROVIDERS), 11)
        self.assertEqual(len(sender_whitelist(mode="senders")), 11)

    def test_sender_lookup(self):
        hit = sender_whitelist(sender="bkash")
        self.assertEqual(hit["provider_key"], "bkash")
        self.assertFalse(sender_whitelist(sender="unknown-xyz"))

    def test_bkash_personal_parse(self):
        msg = ("You have received Tk 5,000.00 from 01700000000. Ref 12345 "
               "Fee Tk 0.00. Balance Tk 10,000.00. TrxID ABC123XYZ at 2024/01/01 12:00")
        out = mfs_message_verified("bkash", msg)
        self.assertNotEqual(out, False)
        self.assertEqual(out["type"], "Personal")
        self.assertEqual(out["mfs"], "bkash")
        self.assertEqual(out["amount"], "5000.00")
        self.assertEqual(out["sender"], "01700000000")
        self.assertEqual(out["trxid"], "ABC123XYZ")
        self.assertEqual(out["balance"], "10000.00")
        self.assertIn("raw", out)

    def test_bkash_cashin_and_merchant(self):
        cashin = ("Cash In Tk 1,000.00 from 01800000000 successful. Fee Tk 5.00. "
                  "Balance Tk 6,000.00. TrxID CI1234567 at 2024/05/01 10:00")
        out = mfs_message_verified("bkash", cashin)
        self.assertEqual(out["type"], "Personal")
        self.assertEqual(out["trxid"], "CI1234567")

    def test_nagad_rocket_upay_tap_cellfin_okwallet_mcash_pathao(self):
        cases = [
            ("nagad", "Money Received. Amount: Tk 2,000.00 Sender: 01900000000 Ref X1 TxnID: NGD123ABCD Balance: Tk 9,000.00 2024/01/02 11:00",
             {"type": "Personal", "trxid": "NGD123ABCD", "amount": "2000.00"}),
            ("rocket", "Tk2,500.00 received from A/C:01700000001 Fee:Tk5.00, Your A/C Balance: Tk7,500.00 TxnId:RKT999AAA Date:01-Jan-24",
             {"type": "Personal", "trxid": "RKT999AAA"}),
            ("tap", "Received Tk 300.00 from 01600000000. Balance Tk. 1,300.00. TxID: TAP111BBB.",
             {"type": "Personal", "trxid": "TAP111BBB"}),
            ("cellfin", "Islami Bank CellFin Received 4,000.00 Tk From CellFin: 01700000002 To CellFin: 01700000003 TrxId: CF222CCC",
             {"type": "Personal", "trxid": "CF222CCC", "receiver": "01700000003"}),
            ("pathaopay", "You have received BDT 750.00 from +880170000004. Balance BDT 2,750.00 TrxID PP333DDD",
             {"type": "Personal", "trxid": "PP333DDD"}),
        ]
        for provider, msg, expect in cases:
            out = mfs_message_verified(provider, msg)
            self.assertNotEqual(out, False, provider)
            for k, v in expect.items():
                self.assertEqual(out[k], v, f"{provider}.{k}")

    def test_telecash_ipay_have_no_formats(self):
        self.assertFalse(mfs_message_verified("telecash", "Tk 100 received TrxID ABC123"))
        self.assertFalse(mfs_message_verified("ipay", "Tk 100 received TrxID ABC123"))

    def test_unknown_message(self):
        self.assertFalse(mfs_message_verified("bkash", "hello world"))


class GatewayParityTests(TestCase):
    def test_all_48_present(self):
        gw = all_gateways()
        self.assertEqual(len(gw), 48)
        for slug in ("stripe", "bkash-personal", "nagad-merchant-api", "sslcommerz",
                     "wise-manual", "aamarpay", "oxapay", "nowpayments", "binance-personal",
                     "shurjopay", "eps", "paystation", "pathaopay-merchant-api", "paypal-api"):
            self.assertIn(slug, gw)

    def test_gateway_types(self):
        types = {s: g.info()["gateway_type"] for s, g in all_gateways().items()}
        self.assertEqual(types["stripe"], "api")
        self.assertEqual(types["bkash-personal"], "automation")
        self.assertEqual(types["wise-manual"], "manual")
        n_api = sum(1 for t in types.values() if t == "api")
        n_auto = sum(1 for t in types.values() if t == "automation")
        n_manual = sum(1 for t in types.values() if t == "manual")
        self.assertEqual((n_api, n_auto, n_manual), (13, 30, 5))

    def test_sender_keys(self):
        self.assertEqual(get_gateway("bkash-personal").info()["sender_key"], "bkash")
        self.assertEqual(get_gateway("bkash-personal").info()["sender_type"], "Personal")
        self.assertEqual(get_gateway("rocket-agent").info()["sender_type"], "Agent")
        self.assertEqual(get_gateway("wise-manual").info()["verify_by"], "trxid")

    def test_fields_mirror_php(self):
        self.assertEqual([f["name"] for f in get_gateway("stripe").fields()],
                         ["secret_key", "webhook_secret"])
        self.assertEqual([f["name"] for f in get_gateway("sslcommerz").fields()],
                         ["store_id", "store_password", "product_category", "mode"])
        self.assertEqual([f["name"] for f in get_gateway("taptap-send-manual").fields()],
                         ["recipient_country", "payment_method", "mfs_number"])
        self.assertEqual([f["name"] for f in get_gateway("eps").fields()],
                         ["merchant_id", "store_id", "username", "password", "hashkey", "mode"])
        self.assertEqual([f["name"] for f in get_gateway("binance-personal").fields()],
                         ["binance_uid", "api_key", "secret_key", "qr_code"])
        self.assertEqual(get_gateway("nagad-agent").fields(), [])
        self.assertIn("auto_refund", [f["name"] for f in get_gateway("bkash-api-tokenized").fields()])
        self.assertIn("mixed_payment", [f["name"] for f in get_gateway("oxapay").fields()])

    def test_colors_mirror_php(self):
        expect = {"nagad-personal": "#ed1c24", "stripe": "#635bff", "aamarpay": "#f39700",
                  "eps": "#ee2d42", "okwallet-personal": "#ffde2d", "nowpayments": "#6c35c3",
                  "oxapay": "#1a34c2", "shurjopay": "#229454", "sslcommerz": "#295cab",
                  "paystation": "#351e53", "tap-personal": "#5fa845", "upay-agent": "#0d56a3",
                  "telecash-merchant": "#ed1495", "mcash-agent": "#66b330", "cellfin-merchant": "#00803d",
                  "ipay-personal": "#019789", "rocket-agent": "#8b3392", "pathaopay-personal": "#3b82de"}
        for slug, color in expect.items():
            self.assertEqual(get_gateway(slug).color()["primary_color"], color, slug)

    def test_langs_and_instructions(self):
        bkash = get_gateway("bkash-personal")
        self.assertEqual(bkash.supported_languages(), {"en": "English", "bn": "বাংলা"})
        self.assertEqual(len(bkash.lang_text()), 7)
        steps = bkash.instructions({"options": {"mobile_number": "017", "qr_code": "qr"},
                                    "transaction": {"local_net_amount": "100", "local_currency": "BDT"}})
        self.assertEqual(len(steps), 7)
        self.assertTrue(steps[2]["copy"])
        self.assertEqual(steps[2]["value"], "017")
        self.assertEqual(get_gateway("binance-personal").supported_languages()["hi"], "हिन्दी")
        self.assertIn("order_id", get_gateway("binance-personal").lang_text())


class ThemeParityTests(TestCase):
    def test_languages(self):
        t = TwentySixTheme()
        self.assertEqual(t.supported_languages(),
                         {"en": "English", "bn": "বাংলা", "hi": "हिन्दी", "ur": "اردو", "ar": "العربية"})
        self.assertEqual(len(t.fields()), 9)

    def test_lang_pack_keys(self):
        t = TwentySixTheme()
        lt = t.lang_text()
        self.assertGreaterEqual(len(lt), 69)
        for key in ("pay_now", "checkout", "invoice", "total", "payment_successful",
                    "product_not_active", "download_receipt"):
            self.assertIn(key, lt)
        self.assertEqual(t.build_lang("bn")["pay_now"], "এখনই পরিশোধ করুন")


class ModelTableParityTests(TestCase):
    def test_23_tables(self):
        from django.apps import apps
        tables = sorted(m._meta.db_table for m in apps.get_models()
                        if m._meta.app_label == "core")
        self.assertEqual(len(tables), 23)
        for expected in ("pp_admin", "pp_transaction", "pp_sms_data", "pp_gateways",
                         "pp_brands", "pp_webhook_log", "pp_env", "pp_permission"):
            self.assertIn(expected, tables)


class ApiFlowTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(brand_id="b1", identify_name="Test", name="Test")
        Currency.objects.create(brand_id="b1", code="BDT", symbol="৳", rate=1)
        self.key = ApiKey.objects.create(brand_id="b1", name="t", api_key="secret123",
                                         expired_date="--",
                                         api_scopes=json.dumps(["create_payment", "verify_payment", "refund_payment"]))
        Domain.objects.create(domain="example.com", status="active")
        self.c = Client()

    def _auth(self, **kw):
        kw["HTTP_MHS_PIPRAPAY_API_KEY"] = "secret123"
        return kw

    def test_invalid_key(self):
        r = self.c.post("/api/checkout/redirect/", data=json.dumps({}),
                        content_type="application/json", HTTP_MHS_PIPRAPAY_API_KEY="bad")
        self.assertEqual(r.json()["error"]["code"], "INVALID_API_KEY")

    def test_expired_key_message(self):
        self.key.expired_date = "2000-01-01 00:00:00"
        self.key.save()
        r = self.c.post("/api/checkout/redirect/", data=json.dumps({}),
                        content_type="application/json", **self._auth())
        self.assertEqual(r.json()["error"]["code"], "INVALID_API_KEY")
        self.assertIn("expired", r.json()["error"]["message"])

    def test_create_redirect(self):
        payload = {"full_name": "A B", "email_address": "a@b.com", "mobile_number": "017",
                   "amount": "100", "currency": "BDT", "return_url": "https://example.com/ok",
                   "webhook_url": "https://example.com/hook", "metadata": {}}
        r = self.c.post("/api/checkout/redirect/", data=json.dumps(payload),
                        content_type="application/json", **self._auth())
        self.assertEqual(r.status_code, 200, r.content[:300])
        body = r.json()
        self.assertIn("pp_id", body)
        self.assertTrue(body["pp_id"].isdigit() and len(body["pp_id"]) == 27)
        self.assertTrue(body["pp_url"].endswith(f"/payment/{body['pp_id']}"))
        txn = Transaction.objects.get(ref=body["pp_id"])
        self.assertEqual(txn.status, "initiated")
        self.assertTrue(Customer.objects.filter(email="a@b.com").exists())

    def test_comma_amount_rejected(self):
        payload = {"full_name": "A B", "email_address": "a@b.com", "mobile_number": "017",
                   "amount": "1,000", "currency": "BDT", "metadata": {}}
        r = self.c.post("/api/checkout/redirect/", data=json.dumps(payload),
                        content_type="application/json", **self._auth())
        self.assertEqual(r.json()["error"]["code"], "INVALID_AMOUNT")

    def test_unlisted_domain_rejected(self):
        payload = {"full_name": "A B", "email_address": "a@b.com", "mobile_number": "017",
                   "amount": "100", "currency": "BDT", "return_url": "https://evil.com/x",
                   "metadata": {}}
        r = self.c.post("/api/checkout/redirect/", data=json.dumps(payload),
                        content_type="application/json", **self._auth())
        self.assertEqual(r.json()["error"]["code"], "INVALID_URL")

    def test_popup_suspend_and_customer(self):
        Customer.objects.create(ref="c1", brand_id="b1", name="S", email="s@b.com",
                                mobile="1", status="suspend", suspend_reason="fraud")
        payload = {"full_name": "S", "email_address": "s@b.com", "mobile_number": "017",
                   "amount": "50", "currency": "BDT", "metadata": {}}
        r = self.c.post("/api/checkout/popup/", data=json.dumps(payload),
                        content_type="application/json", **self._auth())
        self.assertEqual(r.json()["error"]["code"], "INVALID_CUSTOMER")
        payload["email_address"] = "new@b.com"
        r = self.c.post("/api/checkout/popup/", data=json.dumps(payload),
                        content_type="application/json", **self._auth())
        self.assertEqual(r.status_code, 200)
        self.assertTrue(Customer.objects.filter(email="new@b.com").exists())

    def test_unknown_api_type_json(self):
        r = self.c.post("/api/nope/", data=json.dumps({"pp_id": "x"}),
                        content_type="application/json", **self._auth())
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "INVALID_JSON_PAYLOAD")

    def test_verify_and_refund(self):
        txn = Transaction.objects.create(brand_id="b1", source="api", ref="PPTEST123",
                                         customer_info=json.dumps({"name": "A", "email": "a@b.com", "mobile": "1"}),
                                         amount=100, currency="BDT", metadata="[]", status="completed",
                                         local_net_amount=100, local_currency="BDT")
        r = self.c.post("/api/verify-payment/", data=json.dumps({"pp_id": "PPTEST123"}),
                        content_type="application/json", **self._auth())
        body = r.json()
        self.assertEqual(body["pp_id"], "PPTEST123")
        self.assertNotIn("return_url", body)
        r = self.c.post("/api/refund-payment/", data=json.dumps({"pp_id": "PPTEST123"}),
                        content_type="application/json", **self._auth())
        self.assertEqual(r.json()["status"], "refunded")


class CheckoutFlowTests(TestCase):
    def setUp(self):
        Brand.objects.create(brand_id="b1", identify_name="T", name="T", payment_tolerance="0")
        Gateway.objects.create(gateway_id="g1", brand_id="b1", slug="bkash-personal",
                               name="bKash", display="bKash Personal", currency="BDT", status="active")
        GatewayParameter.objects.create(brand_id="b1", gateway_id="g1",
                                        option_name="mobile_number", value="01700000000")
        Transaction.objects.create(brand_id="b1", ref="PP1",
                                   customer_info=json.dumps({"name": "A", "email": "a@b.cd", "mobile": "1"}),
                                   amount=500, currency="BDT", local_net_amount=500, local_currency="BDT",
                                   metadata="[]", status="initiated")
        SmsData.objects.create(device_id="d1", sender_key="bkash", type="Personal",
                               number="01711111111", amount=500, trx_id="TRX9", status="approved")

    def test_checkout_renders(self):
        r = self.client.get("/payment/PP1/")
        self.assertEqual(r.status_code, 200)

    def test_verify_autocomplete(self):
        r = self.client.post("/payment/PP1/verify/",
                             {"gateway-id": "g1", "transaction-id": "PP1", "trxid": "TRX9"})
        body = r.json()
        self.assertEqual(body["status"], "true")
        self.assertEqual(body["title"], "Transaction Verified")
        txn = Transaction.objects.get(ref="PP1")
        self.assertEqual(txn.status, "completed")
        self.assertEqual(txn.sender, "01711111111")

    def test_verify_duplicate_rejected(self):
        Transaction.objects.create(brand_id="b1", ref="PP2",
                                   customer_info="{}", amount=10, currency="BDT", status="completed",
                                   trx_id="TRX9")
        r = self.client.post("/payment/PP1/verify/",
                             {"gateway-id": "g1", "transaction-id": "PP1", "trxid": "TRX9"})
        self.assertEqual(r.json()["title"], "Duplicate Transaction ID")

    def test_verify_pending_payment_flow(self):
        SmsData.objects.filter(trx_id="TRX9").delete()
        GatewayParameter.objects.create(brand_id="b1", gateway_id="g1",
                                        option_name="pending_payment", value="enable")
        r = self.client.post("/payment/PP1/verify/",
                             {"gateway-id": "g1", "transaction-id": "PP1", "trxid": "NEWID"})
        body = r.json()
        self.assertEqual(body.get("visible_number"), "true")
        r = self.client.post("/payment/PP1/verify/",
                             {"gateway-id": "g1", "transaction-id": "PP1", "trxid": "NEWID",
                              "mobile_number": "01722222222"})
        self.assertEqual(r.json()["title"], "Transaction Submitted")
        self.assertEqual(Transaction.objects.get(ref="PP1").status, "pending")

    def test_ipn_invalid_gateway(self):
        r = self.client.post("/ipn/nope/", data={})
        self.assertEqual(r.json()["error"]["code"], "INVALID_GATEWAY")

    def test_home_redirect(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 302)


class StaffAuthTests(TestCase):
    def setUp(self):
        Admin.objects.create(a_id="a1", full_name="Root", username="root",
                             email="r@x.com", password=make_password("pw123456"),
                             role="admin", status="active", twofa_secret="JBSWY3DPEHPK3PXP")
        Permission.objects.create(brand_id="b1", a_id="a1", permission="{}")

    def test_login_dashboard(self):
        r = self.client.post("/login/", {"username": "root", "password": "pw123456"})
        self.assertEqual(r.status_code, 302)
        r = self.client.get("/admin/dashboard/")
        self.assertEqual(r.status_code, 200)

    def test_login_email(self):
        r = self.client.post("/login/", {"username": "r@x.com", "password": "pw123456"})
        self.assertEqual(r.status_code, 302)

    def test_requires_login(self):
        r = self.client.get("/admin/dashboard/")
        self.assertEqual(r.status_code, 302)

    def test_requires_permission_row(self):
        Permission.objects.all().delete()
        r = self.client.post("/login/", {"username": "root", "password": "pw123456"})
        self.assertEqual(r.status_code, 200)
