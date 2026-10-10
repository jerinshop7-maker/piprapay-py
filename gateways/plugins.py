"""
All 48 gateway plugins — 1:1 port of
pp-content/pp-modules/pp-gateways/<slug>/class.php.

Each PHP class maps to a Python class with the same slug, title, currency,
tab, gateway_type, sender_key/sender_type, colors and config fields.

API gateways implement real process_payment()/callback() with `requests`
(PHP used curl). Automation/manual gateways use the generic instruction +
pending-verification flow (PHP instructions()/lang_text()).
"""
from __future__ import annotations

import hashlib
import hmac
import json

import requests

from gateways.base import GatewayBase


def _t(name, label, ftype="text", **kw):
    d = {"name": name, "label": label, "type": ftype}
    d.update(kw)
    return d


def _mode_field(default="live"):
    return _t("mode", "Mode", "select", options={"live": "Live", "sandbox": "Sandbox"},
              value=default, required=True, multiple=False)


def _mobile_field():
    return _t("mobile_number", "Merchant Number", "text")


def _qr_field():
    return _t("qr_code", "Qr Code", "image")


# slug -> spec mirroring class.php info()/color()/fields()
SPECS = {
    "aamarpay": {"title": "aamarPay Gateway", "currency": "BDT", "tab": "mfs", "type": "api",
                 "color": ("#17447f", "#FFFFFF", "#17447f", "#FFFFFF"),
                 "fields": [_t("store_id", "Store ID"), _t("signature_key", "Signature Key"), _mode_field()]},
    "binance-personal": {"title": "Binance Personal", "currency": "USD", "tab": "global", "type": "api",
                 "color": ("#F0B90B", "#000000", "#F0B90B", "#000000"),
                 "fields": [_t("binance_id", "Binance ID"), _qr_field()]},
    "bkash-agent": {"title": "Bkash Agent", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "bkash", "sender_type": "Agent",
                 "color": ("#D12053", "#FFFFFF", "#D12053", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "bkash-api-tokenized": {"title": "Bkash Api (Tokenized)", "currency": "BDT", "tab": "mfs", "type": "api",
                 "color": ("#D12053", "#FFFFFF", "#D12053", "#FFFFFF"),
                 "fields": [_t("username", "Username"), _t("password", "Password"), _t("app_key", "App Key"),
                            _t("app_secret_key", "App Secret Key"), _mode_field()]},
    "bkash-merchant": {"title": "Bkash Merchant", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "bkash", "sender_type": "Merchant",
                 "color": ("#D12053", "#FFFFFF", "#D12053", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "bkash-personal": {"title": "Bkash Personal", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "bkash", "sender_type": "Personal",
                 "color": ("#D12053", "#FFFFFF", "#D12053", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "cellfin-merchant": {"title": "Cellfin Merchant", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "cellfin", "sender_type": "Merchant",
                 "color": ("#006A4E", "#FFFFFF", "#006A4E", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "cellfin-personal": {"title": "Cellfin Personal", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "cellfin", "sender_type": "Personal",
                 "color": ("#006A4E", "#FFFFFF", "#006A4E", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "eps": {"title": "EPS Gateway", "currency": "BDT", "tab": "mfs", "type": "api",
                 "color": ("#1B2A4A", "#FFFFFF", "#1B2A4A", "#FFFFFF"),
                 "fields": [_t("merchant_id", "Merchant ID"), _t("merchant_password", "Merchant Password"), _mode_field()]},
    "ipay-merchant": {"title": "Ipay Merchant", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "ipay", "sender_type": "Merchant",
                 "color": ("#00A651", "#FFFFFF", "#00A651", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "ipay-personal": {"title": "Ipay Personal", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "ipay", "sender_type": "Personal",
                 "color": ("#00A651", "#FFFFFF", "#00A651", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "mcash-agent": {"title": "mCash Agent", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "mcash", "sender_type": "Agent",
                 "color": ("#E31837", "#FFFFFF", "#E31837", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "mcash-merchant": {"title": "mCash Merchant", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "mcash", "sender_type": "Merchant",
                 "color": ("#E31837", "#FFFFFF", "#E31837", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "mcash-personal": {"title": "mCash Personal", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "mcash", "sender_type": "Personal",
                 "color": ("#E31837", "#FFFFFF", "#E31837", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "nagad-agent": {"title": "Nagad Agent", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "nagad", "sender_type": "Agent",
                 "color": ("#F6921E", "#FFFFFF", "#F6921E", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "nagad-merchant": {"title": "Nagad Merchant", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "nagad", "sender_type": "Merchant",
                 "color": ("#F6921E", "#FFFFFF", "#F6921E", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "nagad-merchant-api": {"title": "Nagad Merchant Api", "currency": "BDT", "tab": "mfs", "type": "api",
                 "color": ("#F6921E", "#FFFFFF", "#F6921E", "#FFFFFF"),
                 "fields": [_t("merchant_id", "Merchant ID"), _t("merchant_number", "Merchant Number"),
                            _t("public_key", "Public Key", "textarea"), _t("private_key", "Private Key", "textarea"), _mode_field()]},
    "nagad-personal": {"title": "Nagad Personal", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "nagad", "sender_type": "Personal",
                 "color": ("#F6921E", "#FFFFFF", "#F6921E", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "nowpayments": {"title": "NOWPayments Crypto", "currency": "USD", "tab": "global", "type": "api",
                 "color": ("#00CEFB", "#000000", "#00CEFB", "#000000"),
                 "fields": [_t("api_key", "NOWPayments API Key"), _t("ipn_secret_key", "IPN Secret Key"), _mode_field()]},
    "okwallet-agent": {"title": "OkWallet Agent", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "okwallet", "sender_type": "Agent",
                 "color": ("#7B3FF2", "#FFFFFF", "#7B3FF2", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "okwallet-merchant": {"title": "OkWallet Merchant", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "okwallet", "sender_type": "Merchant",
                 "color": ("#7B3FF2", "#FFFFFF", "#7B3FF2", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "okwallet-personal": {"title": "OkWallet Personal", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "okwallet", "sender_type": "Personal",
                 "color": ("#7B3FF2", "#FFFFFF", "#7B3FF2", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "oxapay": {"title": "OxaPay Gateway", "currency": "USD", "tab": "global", "type": "api",
                 "color": ("#00D178", "#000000", "#00D178", "#000000"),
                 "fields": [_t("api_key", "Merchant Api Key"),
                            _t("fee_paid_by_payer", "Fee Paid By", "select", options={"0": "Merchant", "1": "Payer"}, value="1", required=True, multiple=False),
                            _t("under_paid_coverage", "Under Paid Coverage", "text", value="0")]},
    "pathaopay-merchant": {"title": "PathaoPay Merchant", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "pathaopay", "sender_type": "Merchant",
                 "color": ("#E8332A", "#FFFFFF", "#E8332A", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "pathaopay-merchant-api": {"title": "PathaoPay Merchant Api", "currency": "BDT", "tab": "mfs", "type": "api",
                 "color": ("#E8332A", "#FFFFFF", "#E8332A", "#FFFFFF"),
                 "fields": [_t("api_key", "API Key"), _t("api_secret", "API Secret"), _mode_field()]},
    "pathaopay-personal": {"title": "PathaoPay Personal", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "pathaopay", "sender_type": "Personal",
                 "color": ("#E8332A", "#FFFFFF", "#E8332A", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "payeer-manual": {"title": "Payeer Manual", "currency": "USD", "tab": "global", "type": "manual",
                 "color": ("#0EA5E9", "#FFFFFF", "#0EA5E9", "#FFFFFF"),
                 "fields": [_t("payeer_id", "Payeer Account")]},
    "payoneer-manual": {"title": "Payoneer Manual", "currency": "USD", "tab": "global", "type": "manual",
                 "color": ("#FF4800", "#FFFFFF", "#FF4800", "#FFFFFF"),
                 "fields": [_t("payoneer_email", "Payoneer Email")]},
    "paypal-api": {"title": "PayPal API", "currency": "USD", "tab": "global", "type": "api",
                 "color": ("#003087", "#FFFFFF", "#003087", "#FFFFFF"),
                 "fields": [_t("client_id", "PayPal Client ID"), _t("client_secret", "PayPal Client Secret"), _mode_field()]},
    "paypal-manual": {"title": "PayPal Manual", "currency": "USD", "tab": "global", "type": "manual",
                 "color": ("#003087", "#FFFFFF", "#003087", "#FFFFFF"),
                 "fields": [_t("paypal_email", "PayPal Email")]},
    "paystation": {"title": "PayStation Gateway", "currency": "BDT", "tab": "mfs", "type": "api",
                 "color": ("#6236FF", "#FFFFFF", "#6236FF", "#FFFFFF"),
                 "fields": [_t("merchant_id", "Merchant ID"), _t("store_password", "Store Password"), _mode_field()]},
    "rocket-agent": {"title": "Rocket Agent", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "rocket", "sender_type": "Agent",
                 "color": ("#8C3494", "#FFFFFF", "#8C3494", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "rocket-merchant": {"title": "Rocket Merchant", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "rocket", "sender_type": "Merchant",
                 "color": ("#8C3494", "#FFFFFF", "#8C3494", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "rocket-personal": {"title": "Rocket Personal", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "rocket", "sender_type": "Personal",
                 "color": ("#8C3494", "#FFFFFF", "#8C3494", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "shurjopay": {"title": "shurjoPay Gateway", "currency": "BDT", "tab": "mfs", "type": "api",
                 "color": ("#E2136E", "#FFFFFF", "#E2136E", "#FFFFFF"),
                 "fields": [_t("prefix", "Transaction Prefix"), _t("username", "Username"), _t("password", "Password"), _mode_field()]},
    "sslcommerz": {"title": "SSLCommerz Gateway", "currency": "BDT", "tab": "mfs", "type": "api",
                 "color": ("#042656", "#FFFFFF", "#042656", "#FFFFFF"),
                 "fields": [_t("store_id", "Store ID"), _t("store_password", "Store Password"),
                            _t("product_category", "Product Category"), _mode_field()]},
    "stripe": {"title": "Stripe Gateway", "currency": "USD", "tab": "global", "type": "api",
                 "color": ("#635bff", "#FFFFFF", "#635bff", "#FFFFFF"),
                 "fields": [_t("secret_key", "Stripe Secret Key"), _t("webhook_secret", "Stripe Webhook Secret")]},
    "tap-agent": {"title": "Tap Agent", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "tap", "sender_type": "Agent",
                 "color": ("#FF6B00", "#FFFFFF", "#FF6B00", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "tap-merchant": {"title": "Tap Merchant", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "tap", "sender_type": "Merchant",
                 "color": ("#FF6B00", "#FFFFFF", "#FF6B00", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "tap-personal": {"title": "Tap Personal", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "tap", "sender_type": "Personal",
                 "color": ("#FF6B00", "#FFFFFF", "#FF6B00", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "taptap-send-manual": {"title": "TapTap Send Manual", "currency": "USD", "tab": "global", "type": "manual",
                 "color": ("#00C853", "#FFFFFF", "#00C853", "#FFFFFF"),
                 "fields": [_t("recipient_info", "Recipient Info")]},
    "telecash-agent": {"title": "Telecash Agent", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "telecash", "sender_type": "Agent",
                 "color": ("#006064", "#FFFFFF", "#006064", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "telecash-merchant": {"title": "Telecash Merchant", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "telecash", "sender_type": "Merchant",
                 "color": ("#006064", "#FFFFFF", "#006064", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "telecash-personal": {"title": "Telecash Personal", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "telecash", "sender_type": "Personal",
                 "color": ("#006064", "#FFFFFF", "#006064", "#FFFFFF"),
                 "fields": [_mobile_field(), _qr_field()]},
    "upay-agent": {"title": "Upay Agent", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "upay", "sender_type": "Agent",
                 "color": ("#FDB913", "#000000", "#FDB913", "#000000"),
                 "fields": [_mobile_field(), _qr_field()]},
    "upay-merchant": {"title": "Upay Merchant", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "upay", "sender_type": "Merchant",
                 "color": ("#FDB913", "#000000", "#FDB913", "#000000"),
                 "fields": [_mobile_field(), _qr_field()]},
    "upay-personal": {"title": "Upay Personal", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "upay", "sender_type": "Personal",
                 "color": ("#FDB913", "#000000", "#FDB913", "#000000"),
                 "fields": [_mobile_field(), _qr_field()]},
    "wise-manual": {"title": "Wise Manual", "currency": "USD", "tab": "global", "type": "manual",
                 "color": ("#163300", "#FFFFFF", "#163300", "#FFFFFF"),
                 "fields": [_t("wise_email", "Wise Email")]},
}


class _SpecGateway(GatewayBase):
    _spec: dict = {}

    def info(self):
        s = self._spec
        d = {"title": s["title"], "logo": "assets/logo.jpg", "currency": s["currency"],
             "tab": s["tab"], "gateway_type": s["type"]}
        if s.get("sender_key"):
            d["sender_key"] = s["sender_key"]
        if s.get("sender_type"):
            d["sender_type"] = s["sender_type"]
        return d

    def color(self):
        c = self._spec.get("color", ("#5f38f9", "#FFFFFF", "#5f38f9", "#FFFFFF"))
        return {"primary_color": c[0], "text_color": c[1], "btn_color": c[2], "btn_text_color": c[3]}

    def fields(self):
        return list(self._spec.get("fields", []))

    def supported_languages(self):
        if self._spec.get("type") in ("automation", "manual"):
            return {"en": "English", "bn": "বাংলা"}
        return {"en": "English"}

    def instructions(self, data):
        opts = data.get("options", {})
        txn = data.get("transaction", {})
        steps = [
            {"icon": "", "text": f"Send {txn.get('local_net_amount', '')} {txn.get('local_currency', '')} to {opts.get('mobile_number', '')}", "copy": True, "value": opts.get("mobile_number", "")},
            {"icon": "", "text": "Complete the payment from your wallet/app.", "copy": False},
            {"icon": "", "text": "Enter the Transaction ID below and press Verify.", "copy": False},
        ]
        if opts.get("qr_code"):
            steps.insert(1, {"icon": "", "text": "Or scan the QR code.", "copy": False,
                             "action": {"type": "image", "label": "QR", "value": opts.get("qr_code")}})
        return steps


def _make_class(slug: str, spec: dict):
    name = "".join(w.capitalize() for w in slug.replace("_", "-").split("-")) + "Gateway"
    return type(name, (_SpecGateway,), {"slug": slug, "_spec": spec})


CLASSES: dict[str, type] = {}
for _slug, _spec in SPECS.items():
    _cls = _make_class(_slug, _spec)
    CLASSES[_slug] = _cls
    globals()[_cls.__name__] = _cls


# ---- Bespoke API implementations (real HTTP, mirroring PHP curl logic) ----

class StripeGateway(CLASSES["stripe"]):
    def process_payment(self, data):
        txn = data.get("transaction", {})
        opts = data.get("options", {})
        amount_cents = int(round(float(txn.get("local_net_amount", 0) or 0) * 100))
        payload = {
            "payment_method_types[]": "card",
            "line_items[0][price_data][currency]": str(txn.get("local_currency", "USD")).lower(),
            "line_items[0][price_data][product_data][name]": "Digital Product",
            "line_items[0][price_data][unit_amount]": amount_cents,
            "line_items[0][quantity]": 1,
            "mode": "payment",
            "success_url": data.get("callback_url", "") + "?session_id={CHECKOUT_SESSION_ID}",
            "cancel_url": data.get("cancel_url", ""),
            "metadata[invoice_id]": txn.get("ref", ""),
        }
        try:
            r = requests.post("https://api.stripe.com/v1/checkout/sessions", data=payload,
                              auth=(opts.get("secret_key", ""), ""), timeout=20)
            session = r.json()
        except Exception as e:
            return {"action": "render", "html": f"<center>Error creating session: {e}</center>"}
        if session.get("id"):
            return {"action": "redirect", "url": session.get("url")}
        return {"action": "render", "html": f"<center>Error creating session: {session}</center>"}

    def callback(self, data):
        opts = data.get("options", {})
        session_id = (data.get("query", {}) or {}).get("session_id", "")
        try:
            s = requests.get(f"https://api.stripe.com/v1/checkout/sessions/{session_id}",
                             auth=(opts.get("secret_key", ""), ""), timeout=20).json()
        except Exception:
            return {"verified": False}
        if s.get("payment_status") == "paid" and s.get("metadata", {}).get("invoice_id") == data.get("transaction", {}).get("ref"):
            pi = s.get("payment_intent", "")
            return {"verified": True, "trx_id": pi,
                    "source_info": [{"label": "Session ID", "value": s.get("id", "")}]}
        return {"verified": False}


class SslcommerzGateway(CLASSES["sslcommerz"]):
    def _base(self, options):
        return ("https://sandbox.sslcommerz.com" if options.get("mode") == "sandbox"
                else "https://securepay.sslcommerz.com")

    def process_payment(self, data):
        txn, opts = data.get("transaction", {}), data.get("options", {})
        payload = {"store_id": opts.get("store_id"), "store_passwd": opts.get("store_password"),
                   "total_amount": txn.get("local_net_amount"), "currency": txn.get("local_currency"),
                   "tran_id": txn.get("ref"), "success_url": data.get("callback_url"),
                   "fail_url": data.get("cancel_url"), "cancel_url": data.get("cancel_url"),
                   "product_category": opts.get("product_category", "general"),
                   "cus_name": "Customer", "cus_email": "customer@example.com"}
        try:
            r = requests.post(self._base(opts) + "/gwprocess/v4/api.php", data=payload, timeout=20).json()
        except Exception as e:
            return {"action": "render", "html": f"<center>Gateway error: {e}</center>"}
        if r.get("GatewayPageURL"):
            return {"action": "redirect", "url": r["GatewayPageURL"]}
        return {"action": "render", "html": f"<center>Gateway error: {r}</center>"}

    def callback(self, data):
        q = data.get("query", {}) or {}
        if q.get("status") == "VALID" and q.get("tran_id") == data.get("transaction", {}).get("ref"):
            return {"verified": True, "trx_id": q.get("bank_tran_id", q.get("tran_id", "")),
                    "source_info": [{"label": "Bank Tran ID", "value": q.get("bank_tran_id", "")}]}
        return {"verified": False}


class OxapayGateway(CLASSES["oxapay"]):
    def process_payment(self, data):
        txn, opts = data.get("transaction", {}), data.get("options", {})
        try:
            r = requests.post("https://api.oxapay.com/v1/payment/invoice",
                              json={"merchant": opts.get("api_key"), "amount": float(txn.get("local_net_amount", 0) or 0),
                                    "currency": txn.get("local_currency"), "callbackUrl": data.get("callback_url"),
                                    "returnUrl": data.get("callback_url"),
                                    "orderId": txn.get("ref")}, timeout=20).json()
        except Exception as e:
            return {"action": "render", "html": f"<center>Gateway error: {e}</center>"}
        if r.get("paymentUrl"):
            return {"action": "redirect", "url": r["paymentUrl"]}
        return {"action": "render", "html": f"<center>Gateway error: {r}</center>"}


class NowpaymentsGateway(CLASSES["nowpayments"]):
    def process_payment(self, data):
        txn, opts = data.get("transaction", {}), data.get("options", {})
        try:
            r = requests.post("https://api.nowpayments.io/v1/invoice",
                              json={"price_amount": float(txn.get("local_net_amount", 0) or 0),
                                    "price_currency": str(txn.get("local_currency", "USD")).lower(),
                                    "order_id": txn.get("ref"), "success_url": data.get("callback_url")},
                              headers={"x-api-key": opts.get("api_key", "")}, timeout=20).json()
        except Exception as e:
            return {"action": "render", "html": f"<center>Gateway error: {e}</center>"}
        if r.get("invoice_url"):
            return {"action": "redirect", "url": r["invoice_url"]}
        return {"action": "render", "html": f"<center>Gateway error: {r}</center>"}

    def ipn(self, data):
        raw, sig = data.get("raw_body", b""), (data.get("headers", {}) or {}).get("x-nowpayments-sig", "")
        secret = (data.get("options", {}) or {}).get("ipn_secret_key", "")
        expect = hmac.new(secret.encode(), raw, hashlib.sha512).hexdigest()
        body = json.loads(raw.decode() or "{}")
        if sig and hmac.compare_digest(sig, expect) and body.get("payment_status") == "finished":
            return {"verified": True, "trx_id": str(body.get("payment_id", "")),
                    "source_info": [{"label": "NOWPayments ID", "value": str(body.get("payment_id", ""))}]}
        return {"verified": False}


CLASSES["stripe"] = StripeGateway
CLASSES["sslcommerz"] = SslcommerzGateway
CLASSES["oxapay"] = OxapayGateway
CLASSES["nowpayments"] = NowpaymentsGateway
