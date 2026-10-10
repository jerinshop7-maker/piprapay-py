"""
bKash tokenized refund — port of pp_bkash_tokenized_refund() in pp-functions.php.
"""
from __future__ import annotations

import requests


def _base(mode: str) -> str:
    return ("https://tokenized.pay.bka.sh/v1.2.0-beta" if mode == "live"
            else "https://tokenized.sandbox.bka.sh/v1.2.0-beta")


def bkash_tokenized_refund(transaction, refund: dict) -> dict:
    from core.models import GatewayParameter

    opts = {p.option_name: p.value for p in
            GatewayParameter.objects.filter(gateway_id=transaction.gateway_id)}
    mode = opts.get("mode", "sandbox")
    base = _base(mode)
    try:
        token = requests.post(
            base + "/tokenized/checkout/token/grant",
            json={"app_key": opts.get("app_key"), "app_secret": opts.get("app_secret_key")},
            headers={"username": opts.get("username", ""), "password": opts.get("password", "")},
            timeout=20).json()
        id_token = token.get("id_token", "")
        r = requests.post(
            base + "/tokenized/checkout/payment/refund",
            json={"paymentID": transaction.trx_id, "amount": refund.get("amount"),
                  "trxID": refund.get("sku"), "sku": refund.get("sku"),
                  "reason": refund.get("reason", "API refund")},
            headers={"Authorization": id_token, "X-APP-Key": opts.get("app_key", "")},
            timeout=20).json()
        if r.get("refundTrxId") or r.get("transactionStatus") == "Completed":
            return {"status": True, "data": r}
        return {"status": False, "message": r.get("errorMessage", "Refund failed."), "data": r}
    except Exception as e:
        return {"status": False, "message": str(e)}
