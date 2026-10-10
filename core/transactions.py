"""
Transaction state machine — port of pp_set_transaction_status(),
pp_check_transaction(), pp_check_transaction_id(), pp_gateway_options().
"""
from __future__ import annotations

import json

from core import hooks
from core import money as M
from core.models import Brand, Currency, Gateway, Transaction, WebhookLog
from core.webhooks import send_ipn_multi


def gateway_options(gateway_id: str = "", brand_id: str = "") -> dict:
    from core.models import GatewayParameter

    opts: dict = {}
    for row in GatewayParameter.objects.filter(gateway_id=gateway_id):
        if brand_id and row.brand_id not in (brand_id, "both"):
            continue
        opts[row.option_name] = row.value
    return opts


def transaction_payload(row: Transaction) -> dict:
    try:
        customer = json.loads(row.customer_info or "{}")
    except ValueError:
        customer = {}
    try:
        metadata = json.loads(row.metadata or "{}")
    except ValueError:
        metadata = {}
    gw = Gateway.objects.filter(gateway_id=row.gateway_id, brand_id=row.brand_id).first()
    brand = Brand.objects.filter(brand_id=row.brand_id).first()
    net = M.money_sub(M.money_add(str(row.amount), str(row.processing_fee)), str(row.discount_amount))
    tz = (brand.timezone if brand and brand.timezone not in ("--", "") else "Asia/Dhaka")
    return {
        "pp_id": row.ref,
        "full_name": customer.get("name", "N/A"),
        "email_address": customer.get("email", "N/A"),
        "mobile_number": customer.get("mobile", "N/A"),
        "gateway": gw.display if gw else "",
        "amount": M.money_round(str(row.amount)),
        "fee": M.money_round(str(row.processing_fee)),
        "discount_amount": M.money_round(str(row.discount_amount)),
        "total": M.money_round(net),
        "local_net_amount": M.money_round(str(row.local_net_amount)),
        "currency": row.currency,
        "local_currency": row.local_currency,
        "metadata": metadata,
        "sender": row.sender,
        "transaction_id": row.trx_id,
        "status": row.status,
        "date": M.convert_utc_to_user_tz(row.created_date, tz, "%b %d, %Y %I:%M %p"),
    }


def set_transaction_status(transaction_ref: str, status: str = "", gateway_id: str = "",
                            trx_id: str = "", source_info: list | None = None) -> bool:
    """Mirrors pp_set_transaction_status(). Only 'initiated' rows transition."""
    try:
        row = Transaction.objects.get(ref=transaction_ref, status="initiated")
    except Transaction.DoesNotExist:
        return False

    if status == "canceled":
        row.status = "canceled"
        row.save(update_fields=["status", "updated_date"])
        return True

    if status != "completed":
        return False

    final_source_info = "--"
    if source_info:
        valid = all(isinstance(i, dict) and i.get("label") and i.get("value") for i in source_info)
        if valid:
            final_source_info = json.dumps(source_info, ensure_ascii=False)

    gw = Gateway.objects.filter(gateway_id=gateway_id, brand_id=row.brand_id, status="active").first()
    if not gw:
        return False

    rates = {c.code: M.money_sanitize(str(c.rate)) for c in Currency.objects.filter(brand_id=row.brand_id)}
    txn_amount = M.money_sanitize(str(row.amount))
    if row.currency == gw.currency:
        converted = txn_amount
    else:
        converted = M.money_div(txn_amount, rates[gw.currency]) if gw.currency in rates else "0"

    pct_disc = M.money_div(M.money_mul(converted, M.money_sanitize(str(gw.percentage_discount))), "100")
    total_discount = M.money_add(M.money_sanitize(str(gw.fixed_discount)), pct_disc)
    pct_fee = M.money_div(M.money_mul(converted, M.money_sanitize(str(gw.percentage_charge))), "100")
    total_fee = M.money_add(M.money_sanitize(str(gw.fixed_charge)), pct_fee)
    converted = M.money_add(M.money_sub(converted, total_discount), total_fee)

    if row.currency != gw.currency and gw.currency in rates:
        total_discount = M.money_mul(total_discount, rates[gw.currency])
        total_fee = M.money_mul(total_fee, rates[gw.currency])

    row.processing_fee = total_fee
    row.discount_amount = total_discount
    row.local_net_amount = converted
    row.local_currency = gw.currency
    row.gateway_id = gateway_id
    row.status = "completed"
    row.trx_id = trx_id
    row.source_info = final_source_info
    row.save()

    row.refresh_from_db()
    payload = transaction_payload(row)

    if row.webhook_url and row.webhook_url != "--":
        jobs = [{"id": row.id, "url": row.webhook_url, "payload": payload}]
        results = send_ipn_multi(jobs)
        if results.get(row.id) != 200:
            WebhookLog.objects.create(ref=str(row.id)[:15], brand_id=row.brand_id,
                                      payload=json.dumps(payload), url=row.webhook_url)

    hooks.do_action("transactions.updated", [payload])
    return True


def check_transaction(pp_id: str = ""):
    """Mirrors pp_check_transaction(): returns Transaction or None."""
    return Transaction.objects.filter(ref=pp_id).first()


def check_transaction_id(trx_id: str = ""):
    return Transaction.objects.filter(trx_id=trx_id).first()
