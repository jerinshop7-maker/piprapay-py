"""
Bespoke API-gateway HTTP logic — exact ports of each class.php
process_payment()/callback()/ipn()/refund() (curl -> requests).

Data contract per call:
  process_payment(data)  data={transaction, options, callback_url, cancel_url,
                               ipn_url, query, post}
                         -> {action: redirect|render|form, url|html}
  callback(data)         data={..., query, post}
                         -> {verified, trx_id, source_info[, pp_ref, canceled]}
  ipn(data)              data={..., query, post, headers, raw_body}
                         -> same shape as callback (+ optional pp_ref/redirect/json)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import random
import re
import secrets
import string
from datetime import datetime

import requests

from core import money as M
from core.env import get_env, set_env

TIMEOUT = 20


def _rand_digits(n: int = 10) -> str:
    return "".join(secrets.choice(string.digits) for _ in range(n))


def _float_or_0(value) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------- Stripe
class StripeGateway:
    slug = "stripe"

    def process_payment(self, data):
        txn, opts = data.get("transaction", {}), data.get("options", {})
        payload = {
            "payment_method_types[]": "card",
            "line_items[0][price_data][currency]": str(txn.get("local_currency", "USD")),
            "line_items[0][price_data][product_data][name]": "Digital Product",
            "line_items[0][price_data][unit_amount]": int(round(_float_or_0(txn.get("local_net_amount")) * 100)),
            "line_items[0][quantity]": 1,
            "mode": "payment",
            "success_url": data.get("callback_url", "") + "?session_id={CHECKOUT_SESSION_ID}",
            "cancel_url": data.get("cancel_url", ""),
            "metadata[invoice_id]": txn.get("ref", ""),
        }
        try:
            session = requests.post("https://api.stripe.com/v1/checkout/sessions", data=payload,
                                    auth=(opts.get("secret_key", ""), ""), timeout=TIMEOUT).json()
        except Exception as e:
            return {"action": "render", "html": f"<center>Error creating session: {e}</center>"}
        if session.get("id") and session.get("url"):
            return {"action": "redirect", "url": session["url"]}
        return {"action": "render", "html": f"<center>Error creating session: {session}</center>"}

    def callback(self, data):
        opts = data.get("options", {})
        session_id = (data.get("query", {}) or {}).get("session_id", "")
        auth = (opts.get("secret_key", ""), "")
        try:
            s = requests.get(f"https://api.stripe.com/v1/checkout/sessions/{session_id}",
                             auth=auth, timeout=TIMEOUT).json()
        except Exception:
            return {"verified": False}
        if s.get("payment_status") != "paid":
            return {"verified": False}
        try:
            pi = requests.get(f"https://api.stripe.com/v1/payment_intents/{s.get('payment_intent', '')}",
                              auth=auth, timeout=TIMEOUT).json()
        except Exception:
            return {"verified": False}
        if (pi.get("status") == "succeeded"
                and s.get("metadata", {}).get("invoice_id") == data.get("transaction", {}).get("ref")):
            return {"verified": True, "trx_id": pi.get("id", ""),
                    "source_info": [{"label": "Session ID", "value": s.get("id", "")}]}
        return {"verified": False}


# ------------------------------------------------------------ SSLCommerz
class SslcommerzGateway:
    slug = "sslcommerz"

    def _base(self, options):
        return ("https://sandbox.sslcommerz.com" if options.get("mode") == "sandbox"
                else "https://securepay.sslcommerz.com")

    def process_payment(self, data):
        txn, opts = data.get("transaction", {}), data.get("options", {})
        payload = {"store_id": opts.get("store_id"), "store_passwd": opts.get("store_password"),
                   "total_amount": txn.get("local_net_amount"), "currency": txn.get("local_currency"),
                   "tran_id": _rand_digits(10), "success_url": data.get("callback_url"),
                   "fail_url": data.get("cancel_url"), "cancel_url": data.get("cancel_url"),
                   "ipn_url": data.get("cancel_url"),
                   "emi": 0, "cus_name": "Customer", "cus_email": "customer@example.com",
                   "cus_phone": "01700000000", "cus_add1": "Suite 101", "cus_city": "Dhaka",
                   "cus_postcode": "1207", "cus_country": "Bangladesh",
                   "shipping_method": "NO", "num_of_item": 1,
                   "product_name": "Digital Product",
                   "product_category": opts.get("product_category", "general"),
                   "product_profile": "general",
                   "value_a": txn.get("ref", "")}
        try:
            r = requests.post(self._base(opts) + "/gwprocess/v4/api.php", data=payload,
                              timeout=TIMEOUT, verify=False).json()
        except Exception as e:
            return {"action": "render", "html": f"<center>Gateway error: {e}</center>"}
        if r.get("status") == "SUCCESS" and r.get("GatewayPageURL"):
            return {"action": "redirect", "url": r["GatewayPageURL"]}
        return {"action": "render", "html": f"<center>Gateway error: {r}</center>"}

    def callback(self, data):
        opts = data.get("options", {})
        post = data.get("post", {}) or {}
        tran_id = post.get("tran_id", "")
        if not tran_id:
            return {"verified": False}
        try:
            v = requests.get(
                self._base(opts) + "/validator/api/merchantTransIDvalidationAPI.php",
                params={"tran_id": tran_id, "store_id": opts.get("store_id"),
                        "store_passwd": opts.get("store_password"), "format": "json"},
                timeout=TIMEOUT, verify=False).json()
        except Exception:
            return {"verified": False}
        if v.get("APIConnect") != "DONE" or int(v.get("no_of_trans_found", 0) or 0) <= 0:
            return {"verified": False}
        el = (v.get("element") or [{}])[0]
        if (el.get("status") in ("VALID", "VALIDATED")
                and el.get("value_a") == data.get("transaction", {}).get("ref")):
            return {"verified": True, "trx_id": tran_id,
                    "source_info": [{"label": "val_id", "value": el.get("val_id", "")},
                                    {"label": "Bank GW", "value": el.get("bank_gw", "")},
                                    {"label": "Bank Tran ID", "value": el.get("bank_tran_id", "")}]}
        return {"verified": False}


# ---------------------------------------------------------------- OxaPay
class OxapayGateway:
    slug = "oxapay"

    def process_payment(self, data):
        txn, opts = data.get("transaction", {}), data.get("options", {})
        payload = {"amount": _float_or_0(txn.get("local_net_amount")),
                   "currency": txn.get("local_currency"),
                   "lifetime": 30,
                   "feePaidByPayer": bool(int(str(opts.get("fee_paid_by_payer", "1") or "1"))),
                   "underPaidCoverage": _float_or_0(opts.get("under_paid_coverage", "0")),
                   "toCurrency": "USDT",
                   "autoWithdrawal": False,
                   "mixedPayment": str(opts.get("mixed_payment", "disallow")).lower() == "allow",
                   "callbackUrl": data.get("ipn_url", ""),
                   "returnUrl": data.get("callback_url", ""),
                   "email": "",
                   "orderId": f"{random.randint(10**9, 10**10 - 1)}-BP-{txn.get('ref', '')}",
                   "sandbox": str(opts.get("mode", "live")) != "live"}
        try:
            r = requests.post("https://api.oxapay.com/v1/payment/invoice", json=payload,
                              headers={"merchant_api_key": opts.get("api_key", "")},
                              timeout=TIMEOUT).json()
        except Exception as e:
            return {"action": "render", "html": f"<center>Gateway error: {e}</center>"}
        track_id = (r.get("data") or {}).get("track_id", "") if isinstance(r.get("data"), dict) else ""
        pay_url = (r.get("data") or {}).get("payment_url", "") if isinstance(r.get("data"), dict) else ""
        if track_id and pay_url:
            set_env(f"oxapay-gateway-pp_{txn.get('ref', '')}", track_id)
            return {"action": "redirect", "url": pay_url}
        return {"action": "render", "html": f"<center>Gateway error: {r}</center>"}

    def callback(self, data):
        opts = data.get("options", {})
        track_id = get_env(f"oxapay-gateway-pp_{data.get('transaction', {}).get('ref', '')}")
        if not track_id or track_id == "--":
            return {"verified": False}
        try:
            r = requests.get(f"https://api.oxapay.com/v1/payment/{track_id}",
                             headers={"merchant_api_key": opts.get("api_key", "")},
                             timeout=TIMEOUT).json()
        except Exception:
            return {"verified": False}
        d = r.get("data", {}) if isinstance(r.get("data"), dict) else {}
        order = str(d.get("order_id", ""))
        ref = order.split("-BP-")[1] if "-BP-" in order else ""
        if (d.get("status") == "paid"
                and str(d.get("amount", "")) == str(data.get("transaction", {}).get("local_net_amount", ""))
                and ref == data.get("transaction", {}).get("ref")):
            return {"verified": True, "trx_id": track_id,
                    "source_info": [{"label": "Track ID", "value": track_id}]}
        return {"verified": False}

    def ipn(self, data):
        raw = data.get("raw_body", b"") or b""
        headers = {str(k).lower(): v for k, v in (data.get("headers", {}) or {}).items()}
        got = headers.get("hmac", "")
        expect = hmac.new((data.get("options", {}) or {}).get("api_key", "").encode(),
                          raw, hashlib.sha512).hexdigest()
        if not got or got != expect:
            return {"verified": False, "http_code": 400}
        try:
            body = json.loads(raw.decode() or "{}")
        except ValueError:
            return {"verified": False, "http_code": 400}
        if body.get("type") != "invoice":
            return {"verified": False, "http_code": 400}
        track_id = str(body.get("track_id", ""))
        order = str(body.get("order_id", ""))
        ref = order.split("-BP-")[1] if "-BP-" in order else ""
        # Re-verify server-side like callback().
        status = self.callback({"transaction": {"ref": ref,
                                                "local_net_amount": body.get("amount", "")},
                                "options": data.get("options", {})})
        if status.get("verified"):
            status["pp_ref"] = ref
            return status
        # Fallback: trust paid IPN for the derived ref.
        if track_id and ref:
            return {"verified": True, "trx_id": track_id, "pp_ref": ref,
                    "source_info": [{"label": "Track ID", "value": track_id}]}
        return {"verified": False, "http_code": 400}


# ------------------------------------------------------------ NOWPayments
class NowpaymentsGateway:
    slug = "nowpayments"

    def _base(self, options):
        return ("https://api-sandbox.nowpayments.io/v1" if options.get("mode") == "sandbox"
                else "https://api.nowpayments.io/v1")

    @staticmethod
    def _tksort(obj):
        if isinstance(obj, dict):
            return {k: NowpaymentsGateway._tksort(obj[k]) for k in sorted(obj)}
        if isinstance(obj, list):
            return [NowpaymentsGateway._tksort(x) for x in obj]
        return obj

    def _api(self, method: str, path: str, options, payload=None):
        try:
            r = requests.request(method, self._base(options) + path,
                                 json=payload, headers={"x-api-key": options.get("api_key", "")},
                                 timeout=TIMEOUT)
            return r.json()
        except Exception as e:
            return {"_error": str(e)}

    def process_payment(self, data):
        txn, opts = data.get("transaction", {}), data.get("options", {})
        pay_currency = ((data.get("post", {}) or {}).get("nowpay_currency", "")
                        or (data.get("query", {}) or {}).get("nowpay_currency", ""))
        if not pay_currency:
            # Coin picker grid (mirrors PHP currencies list step).
            currencies = self._api("GET", "/currencies", opts) or {}
            coins = currencies.get("currencies", []) if isinstance(currencies, dict) else []
            items = "".join(
                f'<button type="submit" name="nowpay_currency" value="{c}">{c}</button>'
                for c in (coins[:40] if isinstance(coins, list) else []))
            return {"action": "form",
                    "html": f"<form method='post'><div class='coin-grid'>{items}</div></form>"}
        inv = self._api("POST", "/invoice", opts, {
            "price_amount": _float_or_0(txn.get("local_net_amount")),
            "price_currency": str(txn.get("local_currency", "USD")).lower(),
            "pay_currency": str(pay_currency).lower(),
            "order_id": txn.get("ref", ""),
            "order_description": "Digital Product",
            "ipn_callback_url": data.get("ipn_url", ""),
            "success_url": data.get("callback_url", ""),
            "cancel_url": (data.get("callback_url", "") + "&pp_status=cancel"),
        })
        if inv.get("id") and inv.get("invoice_url"):
            set_env(f"nowpayments-invoice-{txn.get('ref', '')}", str(inv["id"]))
            return {"action": "redirect", "url": inv["invoice_url"]}
        return {"action": "render", "html": f"<center>Gateway error: {inv}</center>"}

    def callback(self, data):
        query = data.get("query", {}) or {}
        if query.get("pp_status") == "cancel":
            return {"verified": False, "canceled": True}
        inv_id = get_env(f"nowpayments-invoice-{data.get('transaction', {}).get('ref', '')}")
        if not inv_id or inv_id == "--":
            return {"verified": False}
        inv = self._api("GET", f"/invoice/{inv_id}", data.get("options", {}))
        status = str(inv.get("payment_status", "") or inv.get("status", ""))
        if status in ("finished", "confirmed"):
            from core.transactions import check_transaction_id
            if check_transaction_id(str(inv.get("payment_id", ""))):
                return {"verified": False}
            return {"verified": True, "trx_id": str(inv.get("payment_id", "")),
                    "source_info": [{"label": "Invoice ID", "value": str(inv.get("id", ""))},
                                    {"label": "Status", "value": status}]}
        return {"verified": False, "pending": status in ("waiting", "confirming", "sending", "partially_paid")}

    def ipn(self, data):
        raw = data.get("raw_body", b"") or b""
        headers = {str(k).lower(): v for k, v in (data.get("headers", {}) or {}).items()}
        secret = (data.get("options", {}) or {}).get("ipn_secret_key", "")
        if not secret:
            return {"verified": False, "http_code": 400}
        try:
            body = json.loads(raw.decode() or "{}")
        except ValueError:
            return {"verified": False, "http_code": 400}
        expect = hmac.new(secret.encode(),
                          json.dumps(self._tksort(body), separators=(",", ":"),
                                     ensure_ascii=False).encode(),
                          hashlib.sha512).hexdigest()
        if not hmac.compare_digest(headers.get("x-nowpayments-sig", ""), expect):
            return {"verified": False, "http_code": 400}
        if str(body.get("payment_status", "")) not in ("finished", "confirmed"):
            return {"verified": False, "http_code": 200}
        from core.transactions import check_transaction, check_transaction_id
        order_id = str(body.get("order_id", ""))
        if not order_id or not check_transaction(order_id):
            return {"verified": False, "http_code": 200}
        if check_transaction_id(str(body.get("payment_id", ""))):
            return {"verified": False, "http_code": 200}
        return {"verified": True, "trx_id": str(body.get("payment_id", "")), "pp_ref": order_id,
                "source_info": [{"label": "Payment ID", "value": str(body.get("payment_id", ""))},
                                {"label": "Paid With", "value": str(body.get("pay_currency", ""))},
                                {"label": "Amount Paid", "value": str(body.get("pay_amount", ""))},
                                {"label": "Pay Address", "value": str(body.get("pay_address", ""))}]}


# ------------------------------------------------------------ aamarPay
class AamarpayGateway:
    slug = "aamarpay"

    def _base(self, options):
        return ("https://sandbox.aamarpay.com" if options.get("mode") == "sandbox"
                else "https://secure.aamarpay.com")

    def process_payment(self, data):
        txn, opts = data.get("transaction", {}), data.get("options", {})
        try:
            customer = json.loads((txn.get("customer_info") or "{}") if isinstance(txn.get("customer_info"), str) else "{}")
        except ValueError:
            customer = {}
        payload = {"store_id": opts.get("store_id"), "tran_id": _rand_digits(12),
                   "success_url": data.get("callback_url", ""), "fail_url": data.get("cancel_url", ""),
                   "cancel_url": data.get("cancel_url", ""),
                   "amount": txn.get("local_net_amount"), "currency": txn.get("local_currency"),
                   "signature_key": opts.get("signature_key"), "desc": "Payment",
                   "cus_name": customer.get("name", "Customer"), "cus_email": customer.get("email", "customer@example.com"),
                   "cus_phone": customer.get("mobile", "01700000000"), "cus_add1": "Dhaka",
                   "cus_city": "Dhaka", "cus_country": "Bangladesh",
                   "type": "json", "opt_a": txn.get("ref", "")}
        try:
            r = requests.post(self._base(opts) + "/jsonpost.php", json=payload, timeout=TIMEOUT).json()
        except Exception as e:
            return {"action": "render", "html": f"<center>Gateway error: {e}</center>"}
        if r.get("payment_url"):
            return {"action": "redirect", "url": r["payment_url"]}
        return {"action": "render", "html": f"<center>Gateway error: {r}</center>"}

    def callback(self, data):
        opts = data.get("options", {})
        post = data.get("post", {}) or {}
        mer_txnid = post.get("mer_txnid", "")
        if not mer_txnid:
            return {"verified": False}
        try:
            r = requests.get(self._base(opts) + "/api/v1/trxcheck/request.php",
                             params={"request_id": mer_txnid, "store_id": opts.get("store_id"),
                                     "signature_key": opts.get("signature_key"), "type": "json"},
                             timeout=TIMEOUT).json()
        except Exception:
            return {"verified": False}
        if (r.get("pay_status") == "Successful" and str(r.get("status_code")) == "2"
                and r.get("opt_a") == data.get("transaction", {}).get("ref")):
            return {"verified": True, "trx_id": str(r.get("bank_trxid", "")),
                    "source_info": [{"label": "aamarPay TransactionId", "value": str(r.get("bank_trxid", ""))},
                                    {"label": "Financial Entity", "value": str(r.get("payment_processor", ""))},
                                    {"label": "PG Transaction ID", "value": str(r.get("pg_txnid", ""))}]}
        return {"verified": False}


# ------------------------------------------------------------ bKash tokenized
class BkashApiTokenizedGateway:
    slug = "bkash-api-tokenized"

    def _base(self, options):
        return ("https://tokenized.pay.bka.sh/v1.2.0-beta/tokenized"
                if (options.get("mode") or "sandbox") == "live"
                else "https://tokenized.sandbox.bka.sh/v1.2.0-beta/tokenized")

    def _grant(self, base: str, options: dict) -> str:
        try:
            r = requests.post(base + "/checkout/token/grant",
                              json={"app_key": options.get("app_key"),
                                    "app_secret": options.get("app_secret_key")},
                              headers={"username": options.get("username", ""),
                                       "password": options.get("password", ""),
                                       "Content-Type": "application/json"},
                              timeout=TIMEOUT).json()
            return r.get("id_token", "")
        except Exception:
            return ""

    def process_payment(self, data):
        txn, opts = data.get("transaction", {}), data.get("options", {})
        base = self._base(opts)
        token = self._grant(base, opts)
        if token:
            set_env(f"bkash-token-{txn.get('ref', '')}", token)
        try:
            r = requests.post(base + "/checkout/create", json={
                "mode": "0011", "amount": txn.get("local_net_amount"),
                "currency": txn.get("local_currency"), "intent": "sale",
                "payerReference": "BillPax",
                "merchantInvoiceNumber": f"{random.randint(10**8, 10**9 - 1)}-BP-{txn.get('ref', '')}",
                "callbackURL": data.get("callback_url", "")},
                headers={"Content-Type": "application/json", "accept": "application/json",
                         "Authorization": token, "X-APP-Key": opts.get("app_key", "")},
                timeout=TIMEOUT).json()
        except Exception as e:
            return {"action": "render", "html": f"<center>Bkash Initialize Error: {e}</center>"}
        if r.get("bkashURL"):
            return {"action": "redirect", "url": r["bkashURL"]}
        return {"action": "render", "html": "<center>Bkash Initialize Error</center>"}

    def callback(self, data):
        opts = data.get("options", {})
        query = data.get("query", {}) or {}
        if query.get("status") == "cancel":
            return {"verified": False, "canceled": True}
        if query.get("status") != "success" or not query.get("paymentID"):
            return {"verified": False}
        token = get_env(f"bkash-token-{data.get('transaction', {}).get('ref', '')}")
        if token == "--":
            token = ""
        try:
            obj = requests.post(self._base(opts) + "/checkout/execute",
                                json={"paymentID": query["paymentID"]},
                                headers={"Content-Type": "application/json",
                                         "Authorization": token or "",
                                         "X-APP-Key": opts.get("app_key", "")},
                                timeout=TIMEOUT).json()
        except Exception:
            return {"verified": False}
        if obj.get("statusMessage") != "Successful":
            return {"verified": False}
        parts = str(obj.get("merchantInvoiceNumber", "")).split("-BP-")
        if len(parts) < 2 or parts[1] != data.get("transaction", {}).get("ref"):
            return {"verified": False}
        return {"verified": True, "trx_id": str(obj.get("trxID", "")),
                "source_info": [{"label": "Payment ID", "value": str(obj.get("paymentID", ""))}]}

    def refund(self, data: dict) -> dict:
        """Exact port of BkashApiTokenizedGateway::refund() + helpers."""
        txn = data.get("transaction", {}) or {}
        options = data.get("options", {}) or {}
        refund = data.get("refund", {}) or {}
        payment_id = refund.get("paymentId", "") or self.extract_payment_id(txn.get("source_info", ""))
        trx_id = txn.get("trx_id", "")
        refund_amount = M.money_round(refund.get("amount", "") or txn.get("local_net_amount", ""))
        sku = refund.get("sku", "") or txn.get("ref", "") or "refund"
        reason = refund.get("reason", "") or "Admin refund"
        if not payment_id:
            return {"status": False, "message": "Payment ID not found for this transaction."}
        if not trx_id or not refund_amount or refund_amount == "0":
            return {"status": False, "message": "Missing required refund fields."}
        base = self._base(options)
        token = self._grant(base, options)
        if not token:
            return {"status": False, "message": "Failed to obtain bKash token."}
        headers = {"Authorization": token, "X-APP-Key": options.get("app_key", "")}
        payload = {"paymentId": payment_id, "trxId": trx_id, "refundAmount": refund_amount,
                   "sku": sku, "reason": reason}
        res = self._post_with_fallback("/v2/tokenized-checkout/refund/payment/transaction",
                                       payload, headers, base)
        refund_data = res.get("data", {}) or {}
        if str(refund_data.get("refundTransactionStatus", "")).lower() == "completed":
            return {"status": True, "message": "Refund completed.", "data": refund_data}
        if res.get("ok") is not True:
            msg = res.get("error", "") or "Refund request failed."
            if res.get("http_code"):
                msg += f" (HTTP {res['http_code']})"
            return {"status": False, "message": msg, "data": refund_data}
        status_res = self._post_with_fallback("/v2/tokenized-checkout/refund/payment/status",
                                              {"paymentId": payment_id, "trxId": trx_id},
                                              headers, res.get("used_base", base))
        status_data = status_res.get("data", {}) or {}
        found = self._find_completed_refund(status_data, refund_amount)
        if found:
            return {"status": True, "message": "Refund completed.", "data": found}
        return {"status": False, "message": "Refund not completed yet.", "data": status_data}

    @staticmethod
    def extract_payment_id(source_info) -> str:
        try:
            items = json.loads(source_info or "[]")
        except ValueError:
            return ""
        if not isinstance(items, list):
            return ""
        for item in items:
            label = str((item or {}).get("label", "")).lower()
            if "payment" in label and "id" in label:
                return str(item.get("value", ""))
        return ""

    def _post_with_fallback(self, path: str, payload: dict, headers: dict, base: str) -> dict:
        for candidate in self._refund_base_candidates(base):
            url = candidate.rstrip("/") + path
            try:
                r = requests.post(url, json=payload,
                                  headers={"Content-Type": "application/json", **headers},
                                  timeout=TIMEOUT)
            except Exception as e:
                last = {"ok": False, "error": str(e), "http_code": 0, "used_base": candidate}
                continue
            try:
                data = r.json()
            except ValueError:
                return {"ok": False, "error": r.text[:200], "http_code": r.status_code,
                        "used_base": candidate, "raw": r.text[:500]}
            if self._is_aws_auth_error(data):
                last = {"ok": False, "error": "AWS auth", "http_code": r.status_code,
                        "used_base": candidate, "data": data}
                continue
            return {"ok": r.status_code < 400, "error": "", "http_code": r.status_code,
                    "used_base": candidate, "data": data if isinstance(data, dict) else {}}
        return last

    @staticmethod
    def _refund_base_candidates(base: str) -> list:
        cands = [base]
        no_tokenized = base.replace("/tokenized", "")
        if no_tokenized not in cands:
            cands.append(no_tokenized)
        m = re.match(r"(https?://[^/]+)((?:/v[\d.]+(?:-beta)?)?)((?:/tokenized)?/?)$", base)
        if m:
            host, ver = m.group(1), m.group(2)
            for v in (ver, "/v1.2.0-beta", ""):
                for t in ("", "/tokenized"):
                    u = host + v + t
                    if u not in cands:
                        cands.append(u)
        return cands

    @staticmethod
    def _is_aws_auth_error(data) -> bool:
        if not isinstance(data, dict):
            return False
        blob = json.dumps(data)
        return "Authorization header requires" in blob and "Credential" in blob

    @staticmethod
    def _find_completed_refund(status_data, amount: str):
        items = []
        if isinstance(status_data, dict):
            for key in ("refunds", "data", "transactions", "items"):
                if isinstance(status_data.get(key), list):
                    items = status_data[key]
                    break
            else:
                items = [status_data]
        for item in items:
            if not isinstance(item, dict):
                continue
            if (str(item.get("refundTransactionStatus", "")).lower() == "completed"
                    and M.money_round(item.get("refundAmount", "")) == M.money_round(amount)):
                return item
        return None


def bkash_tokenized_refund(transaction, refund: dict) -> dict:
    """Exact port of pp_bkash_tokenized_refund() guard chain + delegate."""
    gateway_id = getattr(transaction, "gateway_id", "") or (transaction.get("gateway_id", "") if isinstance(transaction, dict) else "")
    brand_id = getattr(transaction, "brand_id", "") or (transaction.get("brand_id", "") if isinstance(transaction, dict) else "")
    if not gateway_id or not brand_id:
        return {"status": False, "message": "Gateway or brand not found."}
    options = (refund.get("options") or {}) if isinstance(refund, dict) else {}
    if not options:
        from core.transactions import gateway_options
        options = gateway_options(gateway_id, brand_id)
    if not options:
        return {"status": False, "message": "bKash configuration is missing."}
    if options.get("auto_refund") == "off":
        return {"status": False, "message": "Auto refund is disabled for this gateway."}
    txn = transaction if isinstance(transaction, dict) else {
        "gateway_id": transaction.gateway_id, "brand_id": transaction.brand_id,
        "trx_id": transaction.trx_id, "ref": transaction.ref,
        "local_net_amount": str(transaction.local_net_amount), "source_info": transaction.source_info or ""}
    return BkashApiTokenizedGateway().refund({"transaction": txn, "options": options, "refund": refund or {}})


# ---------------------------------------------------------------- EPS
class EpsGateway:
    slug = "eps"

    def _base(self, options):
        return ("https://sandboxpgapi.eps.com.bd" if options.get("mode") == "sandbox"
                else "https://pgapi.eps.com.bd")

    def _token(self, base: str, options: dict) -> str:
        raw = f"{options.get('username', '')}".encode()
        xhash = __import__("base64").b64encode(
            hmac.new(options.get("hashkey", "").encode(), raw, hashlib.sha512).digest()).decode()
        try:
            r = requests.post(base + "/v1/Auth/GetToken",
                              json={"userName": options.get("username"), "password": options.get("password")},
                              headers={"x-hash": xhash}, timeout=TIMEOUT).json()
            return r.get("token", "") or r.get("Token", "")
        except Exception:
            return ""

    def process_payment(self, data):
        txn, opts = data.get("transaction", {}), data.get("options", {})
        base = self._base(opts)
        token = self._token(base, opts)
        if not token:
            return {"action": "render", "html": "<center>EPS auth failed</center>"}
        merchant_txn = f"{int(datetime.now().timestamp())}{random.randint(1000, 9999)}"
        xhash = __import__("base64").b64encode(
            hmac.new(opts.get("hashkey", "").encode(), merchant_txn.encode(), hashlib.sha512).digest()).decode()
        try:
            customer = json.loads(txn.get("customer_info", "{}")) if isinstance(txn.get("customer_info"), str) else {}
        except ValueError:
            customer = {}
        try:
            r = requests.post(base + "/v1/EPSEngine/InitializeEPS", json={
                "merchantId": opts.get("merchant_id"), "storeId": opts.get("store_id"),
                "CustomerOrderId": str(txn.get("id", txn.get("ref", ""))),
                "merchantTransactionId": merchant_txn, "transactionTypeId": 1,
                "totalAmount": _float_or_0(txn.get("local_net_amount")),
                "successUrl": data.get("callback_url", ""), "failUrl": data.get("cancel_url", ""),
                "cancelUrl": data.get("cancel_url", ""),
                "customerName": customer.get("name", "Customer"), "customerEmail": customer.get("email", ""),
                "customerMobile": customer.get("mobile", ""),
                "productName": "Digital Products", "ValueA": txn.get("ref", "")},
                headers={"Authorization": f"Bearer {token}", "x-hash": xhash}, timeout=TIMEOUT).json()
        except Exception as e:
            return {"action": "render", "html": f"<center>Gateway error: {e}</center>"}
        if r.get("RedirectURL"):
            return {"action": "redirect", "url": r["RedirectURL"]}
        return {"action": "render", "html": f"<center>Gateway error: {r}</center>"}

    def callback(self, data):
        opts = data.get("options", {})
        query = data.get("query", {}) or {}
        merchant_txn, eps_txn = query.get("MerchantTransactionId", ""), query.get("EPSTransactionId", "")
        if not merchant_txn or not eps_txn:
            return {"verified": False}
        base = self._base(opts)
        token = self._token(base, opts)
        xhash = __import__("base64").b64encode(
            hmac.new(opts.get("hashkey", "").encode(), merchant_txn.encode(), hashlib.sha512).digest()).decode()
        try:
            r = requests.get(base + "/v1/EPSEngine/CheckMerchantTransactionStatus",
                             params={"merchantTransactionId": merchant_txn, "EPSTransactionId": eps_txn},
                             headers={"Authorization": f"Bearer {token}", "x-hash": xhash},
                             timeout=TIMEOUT).json()
        except Exception:
            return {"verified": False}
        if r.get("Status") == "Success" and r.get("ValueA") == data.get("transaction", {}).get("ref"):
            return {"verified": True, "trx_id": merchant_txn,
                    "source_info": [{"label": "EPS TransactionId", "value": eps_txn},
                                    {"label": "Financial Entity", "value": "EPS"}]}
        return {"verified": False}


# ------------------------------------------------------------ Nagad merchant API
class NagadMerchantApiGateway:
    slug = "nagad-merchant-api"

    def _base(self, options):
        return ("http://sandbox.mynagad.com:10080/remote-payment-gateway-1.0/api/dfs/"
                if (options.get("mode") or "sandbox") != "live"
                else "https://api.mynagad.com/api/dfs/")

    @staticmethod
    def _pub_encrypt(pg_public_key: str, plaintext: bytes) -> str:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        pem = f"-----BEGIN PUBLIC KEY-----\n{pg_public_key.strip()}\n-----END PUBLIC KEY-----".encode()
        key = serialization.load_pem_public_key(pem)
        return __import__("base64").b64encode(key.encrypt(
            plaintext, padding.PKCS1v15())).decode()

    @staticmethod
    def _priv_sign(merchant_private_key: str, plaintext: bytes) -> str:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        pem = f"-----BEGIN RSA PRIVATE KEY-----\n{merchant_private_key.strip()}\n-----END RSA PRIVATE KEY-----".encode()
        key = serialization.load_pem_private_key(pem, password=None)
        return __import__("base64").b64encode(key.sign(
            plaintext, padding.PKCS1v15(), hashes.SHA256())).decode()

    @staticmethod
    def _priv_decrypt(merchant_private_key: str, crypto_b64: str) -> bytes:
        import base64 as _b64
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        pem = f"-----BEGIN RSA PRIVATE KEY-----\n{merchant_private_key.strip()}\n-----END RSA PRIVATE KEY-----".encode()
        key = serialization.load_pem_private_key(pem, password=None)
        return key.decrypt(_b64.b64decode(crypto_b64), padding.PKCS1v15())

    def _post(self, url: str, payload: dict, ip: str = ""):
        try:
            r = requests.post(url, json=payload, headers={
                "Content-Type": "application/json", "X-KM-Api-Version": "v-0.2.0",
                "X-KM-IP-V4": ip, "X-KM-Client-Type": "PC_WEB"}, timeout=TIMEOUT,
                verify=False)
            return r.json()
        except Exception as e:
            return {"error": str(e)}

    def process_payment(self, data):
        txn, opts = data.get("transaction", {}), data.get("options", {})
        query = data.get("query", {}) or {}
        merchant, order_id, status = query.get("merchant", ""), query.get("order_id", ""), query.get("status", "")
        if merchant and order_id and status:
            # Callback return lands on the same page (no callback() method in PHP).
            if status == "Aborted":
                return {"action": "render", "html": "<div>Transaction Canceled</div>", "canceled": True}
            payment_ref_id = query.get("payment_ref_id", "")
            if payment_ref_id:
                verify = self._verify(payment_ref_id, opts)
                if verify.get("status") == "Success":
                    stored = get_env(f"nagad-merchant-api-pp_{verify.get('orderId', '')}")
                    if stored not in ("--", "") and stored == txn.get("ref"):
                        return {"verified": True, "trx_id": str(verify.get("issuerPaymentRefNo", "")),
                                "source_info": [
                                    {"label": "Client Mobile Number", "value": str(verify.get("clientMobileNo", ""))},
                                    {"label": "Service Type", "value": str(verify.get("serviceType", ""))}],
                                "complete_now": True}
            return {"action": "render", "html": "<div>Transaction not valid or not found.</div>"}
        # Initial checkout: initialize + complete (mirrors Xenon RequestHandler).
        try:
            import random as _r
            sess = "".join(_r.choice(string.ascii_uppercase + string.digits) for _ in range(12))
            set_env(f"nagad-merchant-api-pp_{sess}", txn.get("ref", ""))
            now = datetime.now().strftime("%Y%m%d%H%M%S")
            sensitive = {"merchantId": opts.get("merchant_id"), "datetime": now,
                         "orderId": sess, "challenge": self._challenge()}
            pub, priv = opts.get("public_key", ""), opts.get("private_key", "")
            base = self._base(opts)
            init = self._post(f"{base}check-out/initialize/{opts.get('merchant_id')}/{sess}", {
                "accountNumber": opts.get("app_account"), "dateTime": now,
                "sensitiveData": self._pub_encrypt(pub, json.dumps(sensitive).encode()),
                "signature": self._priv_sign(priv, json.dumps(sensitive).encode())})
            if not isinstance(init, dict) or "sensitiveData" not in init:
                return {"action": "render", "html": f"<center>Gateway error: {init}</center>"}
            plain = json.loads(self._priv_decrypt(priv, init["sensitiveData"]).decode())
            order = {"merchantId": opts.get("merchant_id"), "orderId": sess,
                     "currencyCode": "050", "amount": str(round(_float_or_0(txn.get("local_net_amount")))),
                     "challenge": plain.get("challenge", "")}
            done = self._post(f"{base}check-out/complete/{plain.get('paymentReferenceId', '')}", {
                "sensitiveData": self._pub_encrypt(pub, json.dumps(order).encode()),
                "signature": self._priv_sign(priv, json.dumps(order).encode()),
                "merchantCallbackURL": data.get("callback_url", "") + f"?merchant={opts.get('merchant_id')}"})
            if isinstance(done, dict) and done.get("status") == "Success" and done.get("callBackUrl"):
                return {"action": "redirect", "url": done["callBackUrl"]}
            return {"action": "render", "html": f"<center>Gateway error: {done}</center>"}
        except Exception as e:
            return {"action": "render", "html": f"<center>Gateway error: {e}</center>"}

    @staticmethod
    def _challenge() -> str:
        return "you" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(40)) + "me"

    def _verify(self, payment_ref_id: str, opts: dict) -> dict:
        try:
            r = requests.get(self._base(opts) + f"verify/payment/{payment_ref_id}",
                             timeout=TIMEOUT, verify=False)
            v = r.json()
            return v if isinstance(v, dict) else {}
        except Exception:
            return {}


# ------------------------------------------------------------ PathaoPay merchant API
class PathaopayMerchantApiGateway:
    slug = "pathaopay-merchant-api"

    def _base(self, options):
        return ("https://api-stage.pathaopay.com" if options.get("mode") == "sandbox"
                else "https://api.pathaopay.com")

    def process_payment(self, data):
        txn, opts = data.get("transaction", {}), data.get("options", {})
        import uuid as _uuid
        refid = f"ORDER_{_uuid.uuid4().hex[:10].upper()}-BP-{txn.get('ref', '')}"
        try:
            r = requests.post(self._base(opts) + "/api/v1/settlements/request-payment", json={
                "amount": int(round(_float_or_0(txn.get("local_net_amount")) * 100)),
                "merchant_reference_id": refid, "force_otp": False,
                "merchant_callback_url": data.get("callback_url", "")},
                headers={"Application-Key": opts.get("api_key", ""),
                         "Application-Secret": opts.get("secret_key", "")}, timeout=TIMEOUT)
        except Exception as e:
            return {"action": "render", "html": f"<center>Gateway error: {e}</center>"}
        token = r.headers.get("payment-reference-token", "")
        try:
            body = r.json()
        except ValueError:
            body = {}
        redirect_url = (body.get("data") or {}).get("redirect_url", "") if isinstance(body.get("data"), dict) else ""
        invoice_id = (body.get("data") or {}).get("invoice_id", "") if isinstance(body.get("data"), dict) else ""
        if token and redirect_url:
            set_env(f"pathaopay-token-{txn.get('ref', '')}", token)
            set_env(f"pathaopay-invoice-{txn.get('ref', '')}", str(invoice_id))
            return {"action": "redirect", "url": redirect_url}
        return {"action": "render", "html": f"<center>Gateway error: {body}</center>"}

    def callback(self, data):
        opts = data.get("options", {})
        ref = data.get("transaction", {}).get("ref", "")
        token = get_env(f"pathaopay-token-{ref}")
        invoice_id = get_env(f"pathaopay-invoice-{ref}")
        if token in ("--", "") or invoice_id in ("--", ""):
            return {"verified": False}
        try:
            r = requests.post(self._base(opts) + "/api/v1/settlements/request-payment/capture",
                              json={"invoice_id": invoice_id},
                              headers={"Application-Key": opts.get("api_key", ""),
                                       "Application-Secret": opts.get("secret_key", ""),
                                       "Payment-Reference-Token": token},
                              timeout=TIMEOUT)
            ok = r.status_code == 200
            body = r.json() if ok else {}
        except Exception:
            return {"verified": False}
        d = body.get("data", {}) if isinstance(body.get("data"), dict) else {}
        merchant_ref = str(d.get("merchant_reference_id", ""))
        got = merchant_ref.split("-BP-")[1] if "-BP-" in merchant_ref else ""
        if ok and d.get("status") == "success" and got == ref:
            return {"verified": True, "trx_id": str(d.get("transaction_id", "")),
                    "source_info": [{"label": "Invoice ID", "value": str(invoice_id)},
                                    {"label": "Merchant Reference ID", "value": merchant_ref}]}
        return {"verified": False}


# ------------------------------------------------------------ PayPal API
class PaypalApiGateway:
    slug = "paypal-api"

    def _base(self, options):
        return ("https://api-m.sandbox.paypal.com" if options.get("mode") == "sandbox"
                else "https://api-m.paypal.com")

    def _token(self, base: str, options: dict) -> str:
        try:
            r = requests.post(base + "/v1/oauth2/token", data={"grant_type": "client_credentials"},
                              auth=(options.get("client_id", ""), options.get("client_secret", "")),
                              timeout=TIMEOUT)
            return r.json().get("access_token", "")
        except Exception:
            return ""

    def _req(self, method: str, url: str, token: str, payload=None, req_id: str = ""):
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        if req_id:
            headers["PayPal-Request-Id"] = req_id
        if method == "POST":
            headers["Prefer"] = "return=representation"
        try:
            r = requests.request(method, url, json=payload, headers=headers, timeout=TIMEOUT)
            if 200 <= r.status_code < 300:
                return r.json()
            return {"_http": r.status_code, "_body": r.text[:300]}
        except Exception as e:
            return {"_error": str(e)}

    def process_payment(self, data):
        txn, opts = data.get("transaction", {}), data.get("options", {})
        base = self._base(opts)
        token = self._token(base, opts)
        if not token:
            return {"action": "render", "html": "<center>PayPal auth failed</center>"}
        amount = M.money_round(str(txn.get("local_net_amount", "0")))
        order = self._req("POST", base + "/v2/checkout/orders", token, {
            "intent": "CAPTURE",
            "purchase_units": [{"reference_id": txn.get("ref", ""), "custom_id": txn.get("ref", ""),
                                "invoice_id": txn.get("ref", ""), "description": "Digital Product",
                                "amount": {"currency_code": txn.get("local_currency", "USD"), "value": amount}}],
            "payment_source": {"paypal": {"experience_context": {
                "return_url": data.get("callback_url", ""), "cancel_url": data.get("cancel_url", ""),
                "shipping_preference": "NO_SHIPPING", "user_action": "PAY_NOW"}}}},
            req_id=f"{txn.get('ref', '')}-{_rand_digits(6)}")
        for link in order.get("links", []) or []:
            if link.get("rel") in ("approve", "payer-action", "approval_url") and link.get("href"):
                return {"action": "redirect", "url": link["href"]}
        return {"action": "render", "html": f"<center>Gateway error: {order}</center>"}

    def _matches(self, order: dict, txn: dict) -> bool:
        ref = txn.get("ref", "")
        units = order.get("purchase_units", []) or []
        u = units[0] if units else {}
        refs = {u.get("custom_id", ""), u.get("invoice_id", ""), u.get("reference_id", "")}
        if ref not in refs:
            return False
        amt = ((u.get("amount", {}) or {}).get("value", "")) if isinstance(u.get("amount"), dict) else ""
        if not amt and order.get("payments", {}).get("captures"):
            amt = (order["payments"]["captures"][0].get("amount", {}) or {}).get("value", "")
        try:
            if abs(float(str(amt).replace(",", "")) - float(str(txn.get("local_net_amount", "0")).replace(",", ""))) > 0.01:
                return False
        except (ValueError, TypeError):
            return False
        return (u.get("amount", {}) or {}).get("currency_code", txn.get("local_currency")) == txn.get("local_currency")

    def callback(self, data):
        txn, opts = data.get("transaction", {}), data.get("options", {})
        token_id = (data.get("query", {}) or {}).get("token", "")
        if not token_id:
            return {"verified": False}
        base = self._base(opts)
        token = self._token(base, opts)
        order = self._req("GET", base + f"/v2/checkout/orders/{token_id}", token)
        if not self._matches(order, txn):
            return {"verified": False}
        if order.get("status") == "APPROVED":
            order = self._req("POST", base + f"/v2/checkout/orders/{token_id}/capture", token, {},
                              req_id=f"cap-{txn.get('ref', '')}-{_rand_digits(6)}") or order
            if not self._matches(order, txn):
                return {"verified": False}
        if order.get("status") != "COMPLETED":
            return {"verified": False}
        caps = (order.get("payments", {}) or {}).get("captures", []) or []
        cap = caps[0] if caps else {}
        payer = order.get("payer", {}) or {}
        return {"verified": True, "trx_id": cap.get("id", token_id),
                "source_info": [{"label": "Order ID", "value": token_id},
                                {"label": "Payer ID", "value": payer.get("payer_id", "")},
                                {"label": "Payer Email", "value": payer.get("email_address", "")}]}


# ------------------------------------------------------------ PayStation
class PaystationGateway:
    slug = "paystation"

    def _base(self, options):
        return ("https://sandbox.paystation.com.bd" if options.get("mode") == "sandbox"
                else "https://api.paystation.com.bd")

    def process_payment(self, data):
        txn, opts = data.get("transaction", {}), data.get("options", {})
        try:
            r = requests.post(self._base(opts) + "/initiate-payment", data={
                "invoice_number": _rand_digits(10), "currency": "BDT",
                "payment_amount": txn.get("local_net_amount"), "reference": _rand_digits(8),
                "cust_name": "Customer", "cust_phone": "01700000000", "cust_email": "customer@example.com",
                "cust_address": "Bangladesh", "pay_with_charge": opts.get("pay_with_charge", "0"),
                "callback_url": data.get("callback_url", ""),
                "checkout_items": opts.get("checkout_items", "Digital Product"),
                "merchantId": opts.get("merchant_id"), "password": opts.get("merchant_password")},
                timeout=TIMEOUT).json()
        except Exception as e:
            return {"action": "render", "html": f"<center>Gateway error: {e}</center>"}
        if r.get("payment_url"):
            return {"action": "redirect", "url": r["payment_url"]}
        return {"action": "render", "html": f"<center>Gateway error: {r}</center>"}

    def callback(self, data):
        opts = data.get("options", {})
        query = data.get("query", {}) or {}
        if query.get("status") == "Canceled":
            return {"verified": False, "canceled": True}
        invoice_number = query.get("invoice_number", "")
        if not invoice_number:
            return {"verified": False}
        try:
            r = requests.post(self._base(opts) + "/transaction-status",
                              data={"invoice_number": invoice_number},
                              headers={"merchantId": opts.get("merchant_id", "")},
                              timeout=TIMEOUT).json()
        except Exception:
            return {"verified": False}
        if (int(r.get("status_code", 0) or 0) == 200 and str(r.get("status", "")).lower() == "success"
                and str(r.get("trx_status", "")) in ("successful", "Success")):
            return {"verified": True, "trx_id": str(r.get("trx_id", "")),
                    "source_info": [{"label": "Invoice Number", "value": invoice_number},
                                    {"label": "Payer Mobile Number", "value": str(r.get("payer_mobile", ""))},
                                    {"label": "Financial Entity", "value": "PayStation"}]}
        return {"verified": False}


# ------------------------------------------------------------ shurjoPay
class ShurjopayGateway:
    slug = "shurjopay"

    def _base(self, options):
        return ("https://sandbox.shurjopayment.com" if options.get("mode") == "sandbox"
                else "https://engine.shurjopayment.com")

    def _token(self, base: str, options: dict) -> str:
        try:
            r = requests.post(base + "/api/get_token",
                              json={"username": options.get("username"), "password": options.get("password")},
                              timeout=TIMEOUT).json()
            return r.get("token", "")
        except Exception:
            return ""

    def process_payment(self, data):
        txn, opts = data.get("transaction", {}), data.get("options", {})
        base = self._base(opts)
        token = self._token(base, opts)
        if not token:
            return {"action": "render", "html": "<center>shurjoPay auth failed</center>"}
        try:
            r = requests.post(base + "/api/secret-pay", data={
                "prefix": opts.get("prefix", ""), "token": token,
                "return_url": data.get("ipn_url", ""), "cancel_url": data.get("cancel_url", ""),
                "store_id": opts.get("prefix", ""), "amount": txn.get("local_net_amount"),
                "order_id": f"{random.randint(10**7, 10**8 - 1)}-BP-{txn.get('ref', '')}",
                "currency": "BDT", "customer_name": "Customer", "customer_address": "Dhaka",
                "customer_phone": "01700000000", "customer_city": "Dhaka", "customer_post_code": "2113",
                "customer_country": "BD", "client_ip": "102.101.1.1", "discount_amount": 0,
                "disc_percent": 0, "customer_email": "customer@example.com"},
                headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT).json()
        except Exception as e:
            return {"action": "render", "html": f"<center>Gateway error: {e}</center>"}
        if r.get("checkout_url"):
            return {"action": "redirect", "url": r["checkout_url"]}
        return {"action": "render", "html": f"<center>Gateway error: {r}</center>"}

    def ipn(self, data):
        opts = data.get("options", {})
        order_id = (data.get("query", {}) or {}).get("order_id", "")
        if not order_id:
            return {"verified": False, "http_code": 400}
        base = self._base(opts)
        token = self._token(base, opts)
        try:
            r = requests.post(base + "/api/verification", data={"order_id": order_id},
                              headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT).json()
        except Exception:
            return {"verified": False, "http_code": 400}
        rows = r if isinstance(r, list) else [r]
        row = rows[0] if rows and isinstance(rows[0], dict) else {}
        if str(row.get("sp_code")) == "1000" or row.get("bank_status") == "Success":
            cust_order = str(row.get("customer_order_id", ""))
            ref = cust_order.split("-BP-")[1] if "-BP-" in cust_order else ""
            if ref:
                checkout_url = data.get("callback_url", "") or "/"
                return {"verified": True, "trx_id": str(row.get("bank_trx_id", "")), "pp_ref": ref,
                        "redirect": checkout_url,
                        "source_info": [{"label": "PG ID", "value": str(row.get("id", ""))},
                                        {"label": "Order ID", "value": order_id},
                                        {"label": "Method", "value": str(row.get("method", ""))}]}
        # Mark canceled like PHP (handled by caller via canceled flag + pp_ref derivation).
        return {"verified": False, "canceled": True, "http_code": 200}


# ------------------------------------------------------------ Binance personal
class BinancePersonalGateway:
    slug = "binance-personal"

    def process_payment(self, data):
        txn = data.get("transaction", {})
        lang = data.get("lang", {}) or {}
        order_label = lang.get("order_id", "Order ID")
        enter_label = lang.get("enter_order_id", "Enter Order ID")
        verify_label = lang.get("verify", "Verify")
        return {"action": "form", "html": (
            f"<form method='post' action='{data.get('ipn_url', '')}' class='binance-form'>"
            f"<input type='hidden' name='bpid' value='{txn.get('ref', '')}'>"
            f"<label>{order_label}</label>"
            f"<input name='order_id' placeholder='{enter_label}' required>"
            f"<button type='submit'>{verify_label}</button></form>")}

    def ipn(self, data):
        opts = data.get("options", {})
        post = data.get("post", {}) or {}
        order_id = str(post.get("order_id", "")).strip()
        bpid = str(post.get("bpid", "")).strip()
        if not order_id:
            return {"verified": False, "json": {"status": False, "message": "Order ID required."}}
        from core.transactions import check_transaction, check_transaction_id
        if check_transaction_id(order_id):
            return {"verified": False, "json": {"status": False, "message": "Duplicate order."}}
        if not bpid or not check_transaction(bpid):
            return {"verified": False, "json": {"status": False, "message": "Invalid payment."}}
        import time as _time
        params = {"limit": 100, "timestamp": int(_time.time() * 1000)}
        query = "&".join(f"{k}={v}" for k, v in params.items())
        sig = hmac.new(opts.get("secret_key", "").encode(), query.encode(), hashlib.sha256).hexdigest()
        try:
            r = requests.get("https://api.binance.com/sapi/v1/pay/transactions",
                             params={**params, "signature": sig},
                             headers={"X-MBX-APIKEY": opts.get("api_key", "")},
                             timeout=TIMEOUT).json()
        except Exception as e:
            return {"verified": False, "json": {"status": False, "message": str(e)}}
        txns = r.get("data", []) if isinstance(r, dict) else []
        stored = _float_or_0((data.get("transaction") or {}).get("local_net_amount"))
        for t in txns:
            if str(t.get("orderId", "")) == order_id and t.get("currency") == "USDT":
                if stored <= _float_or_0(t.get("amount", 0) or t.get("totalAmount", 0)):
                    number = str(t.get("payerInfo", {}).get("name", "") or t.get("counterparty", ""))
                    return {"verified": True, "trx_id": order_id, "pp_ref": bpid,
                            "json": {"status": True, "visible_number": number},
                            "source_info": [{"label": "Binance Order", "value": order_id}]}
                return {"verified": False, "json": {"status": False, "message": "Amount mismatch."}}
        return {"verified": False, "json": {"status": False, "message": "Order not found."}}


def register(classes: dict) -> None:
    """Overlay bespoke API classes onto the spec-generated CLASSES."""
    for cls in (StripeGateway, SslcommerzGateway, OxapayGateway, NowpaymentsGateway,
                AamarpayGateway, BkashApiTokenizedGateway, EpsGateway,
                NagadMerchantApiGateway, PathaopayMerchantApiGateway, PaypalApiGateway,
                PaystationGateway, ShurjopayGateway, BinancePersonalGateway):
        base = classes.get(cls.slug)
        if base is None:
            continue
        merged = type(cls.__name__, (cls, base), {"slug": cls.slug, "_spec": base._spec})
        classes[cls.slug] = merged
        globals()[cls.__name__] = merged
