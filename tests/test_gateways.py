"""Mocked HTTP tests for the 13 API gateways + checkout math.

Mocks requests.* to replay the provider shapes the PHP code parses, proving
the Django plugins enforce the same verification conditions.
"""
import hashlib
import hmac
import json
from unittest.mock import patch

from django.test import TestCase

from core import money as M
from core.models import Brand, Currency, Gateway, Invoice, InvoiceItem, Transaction
from core.transactions import compute_gateway_totals, invoice_line_totals
from gateways.registry import get_gateway


def _brand():
    b = Brand.objects.create(brand_id="b1", identify_name="B", name="B")
    Currency.objects.create(brand_id="b1", code="BDT", symbol="t", rate=1)
    Currency.objects.create(brand_id="b1", code="USD", symbol="$", rate=117)
    return b


class MockResp:
    def __init__(self, payload, status=200, headers=None):
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class StripeTests(TestCase):
    def test_process_redirect(self):
        gw = get_gateway("stripe")
        with patch("gateways.api_gateways.requests.post",
                   return_value=MockResp({"id": "cs_1", "url": "https://checkout.stripe/pay"})):
            r = gw.process_payment({"transaction": {"local_net_amount": "10", "local_currency": "USD",
                                                    "ref": "R1"},
                                    "options": {"secret_key": "sk"}, "callback_url": "http://x/cb",
                                    "cancel_url": "http://x/c"})
        self.assertEqual(r, {"action": "redirect", "url": "https://checkout.stripe/pay"})

    def test_callback_requires_pi_succeeded_and_ref(self):
        gw = get_gateway("stripe")
        sess = {"payment_status": "paid", "payment_intent": "pi_1", "id": "cs_1",
                "metadata": {"invoice_id": "R1"}}
        with patch("gateways.api_gateways.requests.get") as g:
            g.side_effect = [MockResp(sess), MockResp({"status": "succeeded", "id": "pi_1"})]
            r = gw.callback({"transaction": {"ref": "R1"}, "options": {"secret_key": "sk"},
                             "query": {"session_id": "cs_1"}})
        self.assertTrue(r["verified"])
        self.assertEqual(r["trx_id"], "pi_1")
        # Wrong ref -> reject.
        with patch("gateways.api_gateways.requests.get") as g:
            g.side_effect = [MockResp(sess), MockResp({"status": "succeeded", "id": "pi_1"})]
            r = gw.callback({"transaction": {"ref": "OTHER"}, "options": {"secret_key": "sk"},
                             "query": {"session_id": "cs_1"}})
        self.assertFalse(r["verified"])


class SslcommerzTests(TestCase):
    def test_callback_trusts_only_validator(self):
        gw = get_gateway("sslcommerz")
        # Query says VALID but validator says otherwise -> reject (no forge).
        with patch("gateways.api_gateways.requests.get",
                   return_value=MockResp({"APIConnect": "FAILED", "no_of_trans_found": 0})):
            r = gw.callback({"transaction": {"ref": "R1"}, "options": {},
                             "post": {"tran_id": "T1", "status": "VALID"}})
        self.assertFalse(r["verified"])
        good = {"APIConnect": "DONE", "no_of_trans_found": 1,
                "element": [{"status": "VALIDATED", "value_a": "R1", "val_id": "V",
                             "tran_id": "T1", "bank_gw": "bkash", "bank_tran_id": "BT"}]}
        with patch("gateways.api_gateways.requests.get", return_value=MockResp(good)):
            r = gw.callback({"transaction": {"ref": "R1"}, "options": {},
                             "post": {"tran_id": "T1"}})
        self.assertTrue(r["verified"])
        self.assertEqual(r["trx_id"], "T1")


class OxapayTests(TestCase):
    def test_callback_amount_juggling(self):
        gw = get_gateway("oxapay")
        body = {"data": {"status": "paid", "amount": "100.00", "order_id": "9-BP-R1"}}
        with patch("gateways.api_gateways.requests.get", return_value=MockResp(body)):
            from core.env import set_env
            set_env("oxapay-gateway-pp_R1", "TRK")
            r = gw.callback({"transaction": {"ref": "R1", "local_net_amount": "100"},
                             "options": {"api_key": "k"}})
        self.assertTrue(r["verified"])

    def test_ipn_bad_hmac(self):
        gw = get_gateway("oxapay")
        r = gw.ipn({"raw_body": b"{}", "headers": {"hmac": "nope"},
                    "options": {"api_key": "k"}})
        self.assertFalse(r["verified"])
        self.assertEqual(r["http_code"], 400)


class NowpaymentsTests(TestCase):
    def test_ipn_canonical_hmac(self):
        gw = get_gateway("nowpayments")
        secret = "s3"
        body = {"payment_status": "finished", "order_id": "R1", "payment_id": "P9",
                "pay_currency": "btc", "pay_amount": "0.1", "pay_address": "A"}
        canon = json.dumps(gw._tksort(body), separators=(",", ":"), ensure_ascii=False)
        sig = hmac.new(secret.encode(), canon.encode(), hashlib.sha512).hexdigest()
        Brand.objects.create(brand_id="b1", identify_name="B", name="B")
        Transaction.objects.create(brand_id="b1", ref="R1", customer_info="{}", amount=1,
                                   currency="USD", status="initiated")
        r = gw.ipn({"raw_body": json.dumps(body).encode(),
                    "headers": {"x-nowpayments-sig": sig},
                    "options": {"ipn_secret_key": secret}})
        self.assertTrue(r["verified"])
        self.assertEqual(r["pp_ref"], "R1")
        # Confirmed also accepted; waiting ignored with 200.
        body["payment_status"] = "confirmed"
        canon = json.dumps(gw._tksort(body), separators=(",", ":"), ensure_ascii=False)
        sig = hmac.new(secret.encode(), canon.encode(), hashlib.sha512).hexdigest()
        r = gw.ipn({"raw_body": json.dumps(body).encode(),
                    "headers": {"x-nowpayments-sig": sig},
                    "options": {"ipn_secret_key": secret}})
        self.assertTrue(r["verified"])
        body["payment_status"] = "waiting"
        canon = json.dumps(gw._tksort(body), separators=(",", ":"), ensure_ascii=False)
        sig = hmac.new(secret.encode(), canon.encode(), hashlib.sha512).hexdigest()
        r = gw.ipn({"raw_body": json.dumps(body).encode(),
                    "headers": {"x-nowpayments-sig": sig},
                    "options": {"ipn_secret_key": secret}})
        self.assertFalse(r["verified"])
        self.assertEqual(r["http_code"], 200)


class BkashRefundGuardTests(TestCase):
    def test_guards(self):
        from gateways.bkash_refund import bkash_tokenized_refund
        r = bkash_tokenized_refund({"gateway_id": "", "brand_id": ""}, {})
        self.assertEqual(r["message"], "Gateway or brand not found.")
        r = bkash_tokenized_refund({"gateway_id": "g", "brand_id": "b"},
                                   {"options": {"auto_refund": "off"}})
        self.assertEqual(r["message"], "Auto refund is disabled for this gateway.")

    def test_extract_payment_id(self):
        from gateways.api_gateways import BkashApiTokenizedGateway as B
        src = json.dumps([{"label": "Payment ID", "value": "PAY123"}])
        self.assertEqual(B.extract_payment_id(src), "PAY123")
        self.assertEqual(B.extract_payment_id("[]"), "")

    def test_find_completed_refund(self):
        from gateways.api_gateways import BkashApiTokenizedGateway as B
        data = {"refundTransactions": [
            {"refundTransactionStatus": "Completed", "refundAmount": "100.00",
             "refundTrxId": "RT1"},
            {"refundTransactionStatus": "Pending", "refundAmount": "100.00"}]}
        found = B._find_completed_refund(data, "100")
        self.assertEqual(found["refundTrxId"], "RT1")
        self.assertIsNone(B._find_completed_refund(data, "50"))
        self.assertIsNone(B._find_completed_refund({}, "100"))


class CheckoutMathTests(TestCase):
    def test_vat_is_percent(self):
        _brand()
        inv = Invoice.objects.create(ref="I1", brand_id="b1", customer_info="{}",
                                     currency="BDT", shipping="5", status="unpaid")
        InvoiceItem.objects.create(brand_id="b1", invoice_id="I1", description="x",
                                   amount="100", quantity=2, discount="10", vat="15")
        lines, sums = invoice_line_totals(list(InvoiceItem.objects.filter(invoice_id="I1")))
        # gross 200 - 10 = 190; vat 15% = 28.5; total 218.5
        self.assertEqual(lines[0]["vat"], "28.50")
        self.assertEqual(lines[0]["total"], "218.50")
        self.assertEqual(sums["subtotal"], "200.00000000")

    def test_min_max_gating(self):
        _brand()
        from checkout.views import _gateway_list
        brand = Brand.objects.get(brand_id="b1")
        Gateway.objects.create(gateway_id="g1", brand_id="b1", slug="bkash-personal",
                               currency="BDT", status="active", min_allow=100, max_allow=1000)
        Gateway.objects.create(gateway_id="g2", brand_id="b1", slug="nagad-personal",
                               currency="BDT", status="active", min_allow=10000, max_allow=0)

        class T:
            amount, currency = "500", "BDT"
        shown = _gateway_list(brand, T(), "mfs")
        self.assertEqual([g["gateway_id"] for g in shown], ["g1"])
        # max 0/-- means no upper bound.
        Gateway.objects.filter(gateway_id="g1").update(max_allow=0)
        shown = _gateway_list(brand, T(), "mfs")
        self.assertEqual(len(shown), 1)

    def test_fee_math(self):
        _brand()
        gw = Gateway.objects.create(gateway_id="g1", brand_id="b1", slug="stripe",
                                    currency="USD", status="active", fixed_charge=1,
                                    percentage_charge=2, fixed_discount=0,
                                    percentage_discount=10)
        totals = compute_gateway_totals("100", "BDT", gw, "b1")
        # Literal expectations (PHP semantics: convert to gateway ccy, fee/disc
        # on converted, then fee/disc re-multiplied into txn ccy for storage).
        self.assertEqual(totals["currency"], "USD")
        self.assertEqual(totals["converted"], "1.78632478")
        self.assertEqual(totals["discount"], "9.99999936")
        self.assertEqual(totals["fee"], "118.99999917")


class BankFallbackTests(TestCase):
    def test_bank_steps_lang(self):
        from gateways.render_lang import resolve_bank_steps, framework_built
        steps = resolve_bank_steps({"bank_name": "DBBL", "account_number": "123"},
                                   {"local_net_amount": "1000", "local_currency": "BDT"}, "bn")
        self.assertEqual(len(steps), 8)
        self.assertIn("ব্যাংকের নাম", steps[0]["text"])
        self.assertIn("1,000.00", steps[6]["text"])
        fw = framework_built("bn")
        self.assertEqual(fw["verify"], "যাচাই করুন")
