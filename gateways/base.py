"""
Gateway plugin base — mirrors the PHP gateway class contract in
pp-content/pp-modules/pp-gateways/<slug>/class.php.

Every plugin implements:
  info()  -> {title, logo, currency, tab, gateway_type, sender_key?, sender_type?}
  color() -> {primary_color, text_color, btn_color, btn_text_color}
  fields() -> [{name, label, type, ...}]  (text|image|select|textarea|color)
  supported_languages() / lang_text() / instructions(data)  (automation/manual)
  process_payment(data) / callback(data) / ipn(data)  (api gateways)

gateway_type is one of: api | automation | manual  (identical strings to PHP).
"""
from __future__ import annotations


class GatewayBase:
    slug: str = ""

    def info(self) -> dict:
        raise NotImplementedError

    def color(self) -> dict:
        return {"primary_color": "#5f38f9", "text_color": "#FFFFFF",
                "btn_color": "#5f38f9", "btn_text_color": "#FFFFFF"}

    def fields(self) -> list:
        return []

    def supported_languages(self) -> dict:
        return {"en": "English"}

    def lang_text(self) -> dict:
        return {}

    def instructions(self, data: dict) -> list:
        return []

    # API-type gateways override these:
    def process_payment(self, data: dict) -> dict:
        """Start a payment. Returns {action: redirect|render|qr, url|html}."""
        return {"action": "render", "html": "<p>Gateway processing not configured.</p>"}

    def callback(self, data: dict) -> dict:
        """Handle return/callback. Returns {verified: bool, trx_id: str, source_info: list}."""
        return {"verified": False, "trx_id": "", "source_info": []}

    def ipn(self, data: dict) -> dict:
        """Handle server-to-server IPN. Same return shape as callback()."""
        return self.callback(data)

    # Manual gateways: shopper submits sender/trxid which becomes pending
    def manual_fields(self) -> list:
        return [
            {"name": "sender", "label": "Sender Number", "type": "text"},
            {"name": "trx_id", "label": "Transaction ID", "type": "text"},
        ]
