"""
All 48 gateway plugins — 1:1 port of
pp-content/pp-modules/pp-gateways/<slug>/class.php.

Verified field-by-field against source: titles, colors, tabs, types,
sender_key/sender_type, verify_by, config fields. Language strings and
instruction steps live in gateways/lang_data.json (extracted verbatim from
PHP via tools/extract_gateway_lang.py); bespoke API-gateway HTTP logic lives
in gateways/api_gateways.py.
"""
from __future__ import annotations

import json
from pathlib import Path

from gateways.base import GatewayBase

_LANG_PATH = Path(__file__).with_name("lang_data.json")
try:
    LANG_DATA: dict = json.loads(_LANG_PATH.read_text(encoding="utf-8"))
except OSError:
    LANG_DATA = {}


def _t(name, label, ftype="text", **kw):
    d = {"name": name, "label": label, "type": ftype}
    d.update(kw)
    return d


def _mode_field(default="live"):
    return _t("mode", "Mode", "select", options={"live": "Live", "sandbox": "Sandbox"},
              value=default, required=True, multiple=False)


def automation_extra_fields() -> list:
    """Mirror gateways/edit.php:59-84 auto-injected automation fields."""
    return [
        _t("mobile_number", "Mobile Number", "text", value="", required=True,
           placeholder="Enter mobile number"),
        _t("pending_payment", "Allow Pending Payment?", "select",
           options={"enable": "Enable", "disable": "Disable"},
           value="disable", required=True, multiple=False),
    ]


def bank_fields() -> list:
    """Mirror create-bank.php / edit.php bank-tab fallback fields."""
    return [
        _t("bank_name", "Bank Name", "text", value="", required=True, placeholder="Enter bank name"),
        _t("account_holder_name", "Account Holder Name", "text", value="", required=True,
           placeholder="Enter account holder name"),
        _t("account_number", "Account Number", "text", value="", required=True,
           placeholder="Enter account number"),
        _t("branch_name", "Branch Name", "text", value="", required=True, placeholder="Enter branch name"),
        _t("routing_number", "Routing Number", "text", value="", required=True, placeholder=""),
        _t("swift_code", "SWIFT/BIC Code", "text", value="", required=False, placeholder=""),
    ]


# slug -> spec mirroring class.php info()/color()/fields().
# Colors/fields verified against source (see audit). Gateways without a
# fields() method in PHP have NO config fields here (only automation extras
# are injected at edit time, exactly like PHP).
SPECS = {
    "aamarpay": {"title": "aamarPay Gateway", "currency": "BDT", "tab": "mfs", "type": "api",
                 "color": ("#f39700", "#504e52", "#f39700", "#504e52"),
                 "fields": [_t("store_id", "Store ID"), _t("signature_key", "Signature Key"), _mode_field()]},
    "binance-personal": {"title": "Binance Personal", "currency": "USD", "tab": "global", "type": "api",
                 "color": ("#f0b90b", "#000000", "#f0b90b", "#000000"),
                 "fields": [_t("binance_uid", "Binance UID"), _t("api_key", "Api Key"),
                            _t("secret_key", "Secret Key"), _t("qr_code", "Qr Code", "image")]},
    "bkash-agent": {"title": "Bkash Agent", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "bkash", "sender_type": "Agent",
                 "color": ("#D12053", "#FFFFFF", "#D12053", "#FFFFFF"),
                 "fields": [_t("qr_code", "Qr Code", "image")]},
    "bkash-api-tokenized": {"title": "Bkash Api (Tokenized)", "currency": "BDT", "tab": "mfs", "type": "api",
                 "color": ("#D12053", "#FFFFFF", "#D12053", "#FFFFFF"),
                 "fields": [_t("username", "Username"), _t("password", "Password"), _t("app_key", "App Key"),
                            _t("app_secret_key", "App Secret Key"), _mode_field(),
                            _t("auto_refund", "Auto Refund API", "select",
                               options={"on": "On", "off": "Off"}, value="on", required=False, multiple=False)]},
    "bkash-merchant": {"title": "Bkash Merchant", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "bkash", "sender_type": "Merchant",
                 "color": ("#D12053", "#FFFFFF", "#D12053", "#FFFFFF"),
                 "fields": [_t("qr_code", "Qr Code", "image")]},
    "bkash-personal": {"title": "Bkash Personal", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "bkash", "sender_type": "Personal",
                 "color": ("#D12053", "#FFFFFF", "#D12053", "#FFFFFF"),
                 "fields": [_t("qr_code", "Qr Code", "image")]},
    "cellfin-merchant": {"title": "Cellfin Merchant", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "cellfin", "sender_type": "Merchant",
                 "color": ("#00803d", "#FFFFFF", "#00803d", "#FFFFFF"),
                 "fields": [_t("qr_code", "Qr Code", "image")]},
    "cellfin-personal": {"title": "Cellfin Personal", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "cellfin", "sender_type": "Personal",
                 "color": ("#00803d", "#FFFFFF", "#00803d", "#FFFFFF"),
                 "fields": []},
    "eps": {"title": "EPS Gateway", "currency": "BDT", "tab": "mfs", "type": "api",
                 "color": ("#ee2d42", "#FFFFFF", "#ee2d42", "#FFFFFF"),
                 "fields": [_t("merchant_id", "Merchant ID"), _t("store_id", "Store id"),
                            _t("username", "Username"), _t("password", "Password"),
                            _t("hashkey", "Hash key"), _mode_field()]},
    "ipay-merchant": {"title": "Ipay Merchant", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "ipay", "sender_type": "Merchant",
                 "color": ("#019789", "#FFFFFF", "#019789", "#FFFFFF"),
                 "fields": []},
    "ipay-personal": {"title": "Ipay Personal", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "ipay", "sender_type": "Personal",
                 "color": ("#019789", "#FFFFFF", "#019789", "#FFFFFF"),
                 "fields": []},
    "mcash-agent": {"title": "mCash Agent", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "mcash", "sender_type": "Agent",
                 "color": ("#66b330", "#FFFFFF", "#66b330", "#FFFFFF"),
                 "fields": [_t("qr_code", "Qr Code", "image")]},
    "mcash-merchant": {"title": "mCash Merchant", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "mcash", "sender_type": "Merchant",
                 "color": ("#66b330", "#FFFFFF", "#66b330", "#FFFFFF"),
                 "fields": [_t("qr_code", "Qr Code", "image")]},
    "mcash-personal": {"title": "mCash Personal", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "mcash", "sender_type": "Personal",
                 "color": ("#66b330", "#FFFFFF", "#66b330", "#FFFFFF"),
                 "fields": [_t("qr_code", "Qr Code", "image")]},
    "nagad-agent": {"title": "Nagad Agent", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "nagad", "sender_type": "Agent",
                 "color": ("#ed1c24", "#FFFFFF", "#ed1c24", "#FFFFFF"),
                 "fields": []},
    "nagad-merchant": {"title": "Nagad Merchant", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "nagad", "sender_type": "Merchant",
                 "color": ("#ed1c24", "#FFFFFF", "#ed1c24", "#FFFFFF"),
                 "fields": [_t("qr_code", "Qr Code", "image")]},
    "nagad-merchant-api": {"title": "Nagad Merchant Api", "currency": "BDT", "tab": "mfs", "type": "api",
                 "color": ("#ed1c24", "#FFFFFF", "#ed1c24", "#FFFFFF"),
                 "fields": [_t("app_account", "App Account"), _t("merchant_id", "Merchant ID"),
                            _t("private_key", "Private Key"), _t("public_key", "Public Key"), _mode_field()]},
    "nagad-personal": {"title": "Nagad Personal", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "nagad", "sender_type": "Personal",
                 "color": ("#ed1c24", "#FFFFFF", "#ed1c24", "#FFFFFF"),
                 "fields": []},
    "nowpayments": {"title": "NOWPayments Crypto", "currency": "USD", "tab": "global", "type": "api",
                 "logo": "assets/icon.png",
                 "color": ("#6c35c3", "#FFFFFF", "#6c35c3", "#FFFFFF"),
                 "fields": [_t("api_key", "NOWPayments API Key"), _t("ipn_secret_key", "IPN Secret Key"), _mode_field()]},
    "okwallet-agent": {"title": "OkWallet Agent", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "okwallet", "sender_type": "Agent",
                 "color": ("#ffde2d", "#000000", "#ffde2d", "#000000"),
                 "fields": [_t("qr_code", "Qr Code", "image")]},
    "okwallet-merchant": {"title": "OkWallet Merchant", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "okwallet", "sender_type": "Merchant",
                 "color": ("#ffde2d", "#000000", "#ffde2d", "#000000"),
                 "fields": [_t("qr_code", "Qr Code", "image")]},
    "okwallet-personal": {"title": "OkWallet Personal", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "okwallet", "sender_type": "Personal",
                 "color": ("#ffde2d", "#000000", "#ffde2d", "#000000"),
                 "fields": [_t("qr_code", "Qr Code", "image")]},
    "oxapay": {"title": "OxaPay Gateway", "currency": "USD", "tab": "global", "type": "api",
                 "color": ("#1a34c2", "#FFFFFF", "#1a34c2", "#FFFFFF"),
                 "fields": [_t("api_key", "Merchant Api Key"),
                            _t("fee_paid_by_payer", "Fee Paid By", "select", options={"0": "Merchant", "1": "Payer"}, value="1", required=True, multiple=False),
                            _t("under_paid_coverage", "Under Paid Coverage", "text", value="0"),
                            _t("mixed_payment", "Mixed Payment", "select", options={"allow": "Allow", "disallow": "Disallow"}, value="disallow", required=True, multiple=False),
                            _mode_field()]},
    "pathaopay-merchant": {"title": "PathaoPay Merchant", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "pathaopay", "sender_type": "Merchant",
                 "color": ("#3b82de", "#FFFFFF", "#3b82de", "#FFFFFF"),
                 "fields": [_t("qr_code", "Qr Code", "image")]},
    "pathaopay-merchant-api": {"title": "PathaoPay Merchant Api", "currency": "BDT", "tab": "mfs", "type": "api",
                 "color": ("#3b82de", "#FFFFFF", "#3b82de", "#FFFFFF"),
                 "fields": [_t("api_key", "Api Key"), _t("secret_key", "Secret Key"), _mode_field()]},
    "pathaopay-personal": {"title": "PathaoPay Personal", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "pathaopay", "sender_type": "Personal",
                 "color": ("#3b82de", "#FFFFFF", "#3b82de", "#FFFFFF"),
                 "fields": [_t("qr_code", "Qr Code", "image")]},
    "payeer-manual": {"title": "Payeer Manual", "currency": "USD", "tab": "global", "type": "manual",
                 "verify_by": "trxid",
                 "color": ("#64b3df", "#FFFFFF", "#64b3df", "#FFFFFF"),
                 "fields": [_t("recipient_payeer_account", "Recipient Payeer Account")]},
    "payoneer-manual": {"title": "Payoneer Manual", "currency": "USD", "tab": "global", "type": "manual",
                 "verify_by": "trxid",
                 "color": ("#252526", "#FFFFFF", "#252526", "#FFFFFF"),
                 "fields": [_t("payoneer_email", "Enter payoneer email address")]},
    "paypal-api": {"title": "PayPal API", "currency": "USD", "tab": "global", "type": "api",
                 "color": ("#003087", "#FFFFFF", "#003087", "#FFFFFF"),
                 "fields": [_t("client_id", "PayPal Client ID"), _t("client_secret", "PayPal Client Secret"), _mode_field()]},
    "paypal-manual": {"title": "PayPal Manual", "currency": "USD", "tab": "global", "type": "manual",
                 "verify_by": "trxid",
                 "color": ("#253b80", "#FFFFFF", "#253b80", "#FFFFFF"),
                 "fields": [_t("paypal_email", "Enter paypal email address")]},
    "paystation": {"title": "PayStation Gateway", "currency": "BDT", "tab": "mfs", "type": "api",
                 "color": ("#351e53", "#FFFFFF", "#351e53", "#FFFFFF"),
                 "fields": [_t("merchant_id", "Merchant ID"), _t("merchant_password", "Merchant Password"),
                            _t("checkout_items", "Checkout items"),
                            _t("pay_with_charge", "Who pay fees?", "select", options={"0": "Customer", "1": "Merchant"}, value="0", required=True, multiple=False),
                            _mode_field()]},
    "rocket-agent": {"title": "Rocket Agent", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "rocket", "sender_type": "Agent",
                 "color": ("#8b3392", "#FFFFFF", "#8b3392", "#FFFFFF"),
                 "fields": [_t("qr_code", "Qr Code", "image")]},
    "rocket-merchant": {"title": "Rocket Merchant", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "rocket", "sender_type": "Merchant",
                 "color": ("#8b3392", "#FFFFFF", "#8b3392", "#FFFFFF"),
                 "fields": [_t("qr_code", "Qr Code", "image")]},
    "rocket-personal": {"title": "Rocket Personal", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "rocket", "sender_type": "Personal",
                 "color": ("#8b3392", "#FFFFFF", "#8b3392", "#FFFFFF"),
                 "fields": [_t("qr_code", "Qr Code", "image")]},
    "shurjopay": {"title": "shurjoPay Gateway", "currency": "BDT", "tab": "mfs", "type": "api",
                 "color": ("#229454", "#FFFFFF", "#229454", "#FFFFFF"),
                 "fields": [_t("prefix", "Transaction Prefix"), _t("username", "Username"), _t("password", "Password"), _mode_field()]},
    "sslcommerz": {"title": "SSLCommerz Gateway", "currency": "BDT", "tab": "mfs", "type": "api",
                 "color": ("#295cab", "#FFFFFF", "#295cab", "#FFFFFF"),
                 "fields": [_t("store_id", "Store ID"), _t("store_password", "Store Password"),
                            _t("product_category", "Product Category"), _mode_field()]},
    "stripe": {"title": "Stripe Gateway", "currency": "USD", "tab": "global", "type": "api",
                 "color": ("#635bff", "#FFFFFF", "#635bff", "#FFFFFF"),
                 "fields": [_t("secret_key", "Stripe Secret Key"), _t("webhook_secret", "Stripe Webhook Secret")]},
    "tap-agent": {"title": "Tap Agent", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "tap", "sender_type": "Agent",
                 "color": ("#5fa845", "#FFFFFF", "#5fa845", "#FFFFFF"),
                 "fields": []},
    "tap-merchant": {"title": "Tap Merchant", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "tap", "sender_type": "Merchant",
                 "color": ("#5fa845", "#FFFFFF", "#5fa845", "#FFFFFF"),
                 "fields": [_t("qr_code", "Qr Code", "image")]},
    "tap-personal": {"title": "Tap Personal", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "tap", "sender_type": "Personal",
                 "color": ("#5fa845", "#FFFFFF", "#5fa845", "#FFFFFF"),
                 "fields": []},
    "taptap-send-manual": {"title": "TapTap Send Manual", "currency": "USD", "tab": "global", "type": "manual",
                 "verify_by": "trxid",
                 "color": ("#03691f", "#FFFFFF", "#03691f", "#FFFFFF"),
                 "fields": [_t("recipient_country", "Enter recipient country"),
                            _t("payment_method", "Payment Method", "select",
                               options={"Bkash": "Bkash", "Nagad": "Nagad", "Rocket": "Rocket", "Upay": "Upay"},
                               value="Bkash", required=False, multiple=False),
                            _t("mfs_number", "MFS Number")]},
    "telecash-agent": {"title": "Telecash Agent", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "telecash", "sender_type": "Agent",
                 "color": ("#ed1495", "#FFFFFF", "#ed1495", "#FFFFFF"),
                 "fields": [_t("qr_code", "Qr Code", "image")]},
    "telecash-merchant": {"title": "Telecash Merchant", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "telecash", "sender_type": "Merchant",
                 "color": ("#ed1495", "#FFFFFF", "#ed1495", "#FFFFFF"),
                 "fields": [_t("qr_code", "Qr Code", "image")]},
    "telecash-personal": {"title": "Telecash Personal", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "telecash", "sender_type": "Personal",
                 "color": ("#ed1495", "#FFFFFF", "#ed1495", "#FFFFFF"),
                 "fields": [_t("qr_code", "Qr Code", "image")]},
    "upay-agent": {"title": "Upay Agent", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "upay", "sender_type": "Agent",
                 "color": ("#0d56a3", "#FFFFFF", "#0d56a3", "#FFFFFF"),
                 "fields": [_t("qr_code", "Qr Code", "image")]},
    "upay-merchant": {"title": "Upay Merchant", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "upay", "sender_type": "Merchant",
                 "color": ("#0d56a3", "#FFFFFF", "#0d56a3", "#FFFFFF"),
                 "fields": [_t("qr_code", "Qr Code", "image")]},
    "upay-personal": {"title": "Upay Personal", "currency": "BDT", "tab": "mfs", "type": "automation",
                 "sender_key": "upay", "sender_type": "Personal",
                 "color": ("#0d56a3", "#FFFFFF", "#0d56a3", "#FFFFFF"),
                 "fields": [_t("qr_code", "Qr Code", "image")]},
    "wise-manual": {"title": "Wise Manual", "currency": "USD", "tab": "global", "type": "manual",
                 "verify_by": "trxid",
                 "color": ("#163300", "#FFFFFF", "#163300", "#FFFFFF"),
                 "fields": [_t("recipient_wise_account", "Recipient Wise Account")]},
}


class _SpecGateway(GatewayBase):
    _spec: dict = {}

    @property
    def _lang(self) -> dict:
        return LANG_DATA.get(self.slug, {})

    def info(self):
        s = self._spec
        d = {"title": s["title"], "logo": s.get("logo", "assets/logo.jpg"), "currency": s["currency"],
             "tab": s["tab"], "gateway_type": s["type"]}
        if s.get("sender_key"):
            d["sender_key"] = s["sender_key"]
        if s.get("sender_type"):
            d["sender_type"] = s["sender_type"]
        if s.get("verify_by"):
            d["verify_by"] = s["verify_by"]
        return d

    def color(self):
        c = self._spec.get("color", ("#5f38f9", "#FFFFFF", "#5f38f9", "#FFFFFF"))
        return {"primary_color": c[0], "text_color": c[1], "btn_color": c[2], "btn_text_color": c[3]}

    def fields(self):
        return list(self._spec.get("fields", []))

    def supported_languages(self) -> dict:
        langs = self._lang.get("supported_languages")
        if isinstance(langs, dict) and langs:
            return dict(langs)
        if self._spec.get("type") in ("automation", "manual"):
            return {"en": "English", "bn": "বাংলা"}
        return {"en": "English"}

    def lang_text(self) -> dict:
        lt = self._lang.get("lang_text")
        return dict(lt) if isinstance(lt, dict) else {}

    def instructions(self, data: dict) -> list:
        """Rebuild PHP instructions() from extracted step specs + live data."""
        out = []
        for spec in self._lang.get("instructions", []):
            step = {"icon": "", "text": spec.get("text", "")}
            if "copy" in spec:
                step["copy"] = spec["copy"]
            if spec.get("value"):
                scope, key = spec["value"]
                step["value"] = (data.get(scope, {}) or {}).get(key, "")
            if spec.get("action"):
                step["action"] = {"type": spec["action"], "label": "",
                                  "value": (data.get((spec.get("action_value") or [None, None])[0], {}) or {}).get(
                                      (spec.get("action_value") or [None, None])[1], "")}
            if spec.get("vars"):
                step["vars"] = {f"{{{v}}}": self._var_value(v, data) for v in spec["vars"]}
            out.append(step)
        return out

    @staticmethod
    def _var_value(var: str, data: dict) -> str:
        opts = data.get("options", {}) or {}
        txn = data.get("transaction", {}) or {}
        if var in opts:
            return str(opts.get(var, ""))
        mapping = {"amount": txn.get("local_net_amount", ""), "currency": txn.get("local_currency", "")}
        return str(mapping.get(var, opts.get(var, "")))


def _make_class(slug: str, spec: dict):
    name = "".join(w.capitalize() for w in slug.replace("_", "-").split("-")) + "Gateway"
    return type(name, (_SpecGateway,), {"slug": slug, "_spec": spec})


CLASSES: dict[str, type] = {}
for _slug, _spec in SPECS.items():
    _cls = _make_class(_slug, _spec)
    CLASSES[_slug] = _cls
    globals()[_cls.__name__] = _cls

# Bespoke API implementations (exact HTTP ports) register over the specs.
from gateways.api_gateways import register as _register_api  # noqa: E402

_register_api(CLASSES)
