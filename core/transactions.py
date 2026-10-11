"""
Transaction engine — exact port of pp_set_transaction_status(),
pp_check_transaction(), pp_get_gateway_options(), the transaction-verify
branches (pp-adapter.php:8758-9155 shared math), and the invoice webhook
receiver (index.php:1195-1290).
"""
from __future__ import annotations

import json

from core import hooks
from core import money as M
from core.models import Brand, Currency, Gateway, Invoice, InvoiceItem, Transaction, WebhookLog
from core.webhooks import send_ipn_multi


def gateway_options(gateway_id: str = "", brand_id: str | None = "") -> dict:
    """Render/verify paths query by gateway_id only (pp_gateway_render,
    transaction-verify); the refund path passes both ids
    (pp_get_gateway_options requires both)."""
    from core.models import GatewayParameter

    if not gateway_id:
        return {}
    qs = GatewayParameter.objects.filter(gateway_id=gateway_id)
    if brand_id:
        qs = qs.filter(brand_id=brand_id)
    return {p.option_name: p.value for p in qs}


def compute_gateway_totals(amount: str, txn_currency: str, gateway: Gateway, brand_id: str) -> dict:
    """Shared FX + fee/discount math (verify + set_transaction_status + listing).

    Mirrors the repeated PHP block exactly, including the cross-currency
    re-multiplication of fee/discount by the rate.
    """
    rates = {c.code: M.money_sanitize(str(c.rate)) for c in Currency.objects.filter(brand_id=brand_id)}
    txn_amount = M.money_sanitize(amount)
    if txn_currency == gateway.currency:
        converted = txn_amount
    elif gateway.currency in rates:
        converted = M.money_div(txn_amount, rates[gateway.currency])
    else:
        converted = "0"

    pct_disc = M.money_div(M.money_mul(converted, M.money_sanitize(str(gateway.percentage_discount)), 8), "100", 8)
    total_discount = M.money_add(M.money_sanitize(str(gateway.fixed_discount)), pct_disc, 8)
    pct_fee = M.money_div(M.money_mul(converted, M.money_sanitize(str(gateway.percentage_charge)), 8), "100", 8)
    total_fee = M.money_add(M.money_sanitize(str(gateway.fixed_charge)), pct_fee, 8)
    converted = M.money_add(M.money_sub(converted, total_discount, 8), total_fee, 8)

    if txn_currency != gateway.currency and gateway.currency in rates:
        total_discount = M.money_mul(total_discount, rates[gateway.currency])
        total_fee = M.money_mul(total_fee, rates[gateway.currency])
    return {"converted": converted, "discount": total_discount, "fee": total_fee,
            "currency": gateway.currency, "rates": rates}


def transaction_payload(row: Transaction, name_field: str = "display",
                        round_total: bool = False) -> dict:
    """16-field payload. API verify/refund use gateway `display` + ROUNDED total
    (index.php:827-845); transaction-verify uses `name` + unrounded total."""
    try:
        customer = json.loads(row.customer_info or "{}")
        if not isinstance(customer, dict):
            customer = {}
    except ValueError:
        customer = {}
    try:
        metadata = json.loads(row.metadata or "{}")
    except ValueError:
        metadata = {}
    gw = Gateway.objects.filter(gateway_id=row.gateway_id, brand_id=row.brand_id).first()
    brand = Brand.objects.filter(brand_id=row.brand_id).first()
    gw_label = ""
    if gw:
        gw_label = (gw.display if name_field == "display" else gw.name) or ""

    def _or_na(value):
        return value if value is not None else "N/A"

    total = M.money_sub(M.money_add(str(row.amount), str(row.processing_fee)), str(row.discount_amount))
    if round_total:
        total = M.money_round(total)
    tz = norm_tz(brand.timezone if brand else "")
    return {
        "pp_id": row.ref,
        "full_name": _or_na(customer.get("name", "N/A")),
        "email_address": _or_na(customer.get("email", "N/A")),
        "mobile_number": _or_na(customer.get("mobile", "N/A")),
        "gateway": gw_label,
        "amount": M.money_round(str(row.amount)),
        "fee": M.money_round(str(row.processing_fee)),
        "discount_amount": M.money_round(str(row.discount_amount)),
        "total": total,
        "local_net_amount": M.money_round(str(row.local_net_amount)),
        "currency": row.currency,
        "local_currency": row.local_currency,
        "metadata": metadata,
        "sender": row.sender,
        "transaction_id": row.trx_id,
        "status": row.status,
        "date": M.convert_utc_to_user_tz(row.created_date, tz, "%b %d, %Y %I:%M %p"),
    }


def norm_tz(tz) -> str:
    """'--'/''/None timezone -> Asia/Dhaka (index.php pattern)."""
    if not tz or tz == "--":
        return "Asia/Dhaka"
    return tz


def queue_or_send_webhook(row: Transaction, payload: dict) -> None:
    """Immediate sendIPNMulti; on non-200 queue into webhook_log with a random
    int ref (PHP rand()), like pp_set_transaction_status/transaction-verify."""
    if not row.webhook_url or row.webhook_url == "--":
        return
    import random
    jobs = [{"id": random.randint(1, 2 ** 31 - 1), "url": row.webhook_url, "payload": payload}]
    results = send_ipn_multi(jobs)
    if results.get(jobs[0]["id"]) != 200:
        WebhookLog.objects.create(ref=random.randint(1, 2 ** 31 - 1), brand_id=row.brand_id,
                                  payload=json.dumps(payload, ensure_ascii=False), url=row.webhook_url)


def set_transaction_status(transaction_ref: str, status: str = "", gateway_id: str = "",
                            trx_id: str = "", source_info: list | None = None) -> bool:
    """Mirror pp_set_transaction_status(). Only 'initiated' rows transition."""
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

    totals = compute_gateway_totals(str(row.amount), row.currency, gw, row.brand_id)
    row.processing_fee = totals["fee"]
    row.discount_amount = totals["discount"]
    row.local_net_amount = totals["converted"]
    row.local_currency = totals["currency"]
    row.gateway_id = gateway_id
    row.status = "completed"
    row.trx_id = trx_id
    row.source_info = final_source_info
    row.save()
    row.refresh_from_db()

    payload = transaction_payload(row)
    queue_or_send_webhook(row, payload)
    hooks.do_action("transactions.updated", [payload])
    return True


def build_return_url(base_url: str, status: str, ref: str) -> str:
    """Mirror index.php:1081-1089 finalUrl logic."""
    if status == "initiated":
        return "--"
    if not base_url or base_url == "--":
        return "--"
    return M.add_query_params(base_url, {"pp_status": status, "transaction_ref": ref})


def verify_transaction_post(*, gateway_id: str, transaction_ref: str, trxid: str = "",
                            mobile_number: str = "", slip_file=None, site_base: str = "/") -> dict:
    """Mirror the transaction-verify handler (pp-adapter.php:8758-9155).

    Returns the exact {status,title,message[,visible_number]} JSON shape with
    PHP's Code 101-106 error titles.
    """
    from core.models import SmsData

    def fail(title, message):
        return {"status": "false", "title": title, "message": message}

    if not gateway_id or not transaction_ref:
        return fail("Incomplete Information", "Please fill in all required fields before proceeding.")

    txn = Transaction.objects.filter(ref=transaction_ref, status="initiated").first()
    if not txn:
        return fail("Request Failed. Code 101", "Please fill in all required fields before proceeding.")

    brand = Brand.objects.filter(brand_id=txn.brand_id).first()
    if not brand:
        return fail("Request Failed. Code 102", "Please fill in all required fields before proceeding.")

    gw = Gateway.objects.filter(gateway_id=gateway_id, brand_id=brand.brand_id, status="active").first()
    if not gw:
        return fail("Request Failed. Code 103", "Please fill in all required fields before proceeding.")

    from gateways.registry import get_gateway
    plugin = get_gateway(gw.slug)
    if plugin is not None:
        gateway_info = plugin.info()
    elif gw.tab == "bank":
        gateway_info = {"gateway_type": "manual", "verify_by": "slip"}
    else:
        return fail("Request Failed. Code 104", "Please fill in all required fields before proceeding.")

    totals = compute_gateway_totals(str(txn.amount), txn.currency, gw, brand.brand_id)
    options = gateway_options(gateway_id, brand.brand_id)
    gtype = gateway_info.get("gateway_type")

    if gtype == "automation":
        if not trxid:
            return fail("Missing Transaction ID",
                        "The Transaction ID field cannot be empty. Please provide a valid Transaction ID.")
        if Transaction.objects.filter(trx_id=trxid).exists():
            return fail("Duplicate Transaction ID",
                        "This Transaction ID is already exits. Please provide a different one.")
        sms = SmsData.objects.filter(sender_key=gateway_info.get("sender_key", ""),
                                     type=gateway_info.get("sender_type", ""),
                                     trx_id=trxid, status="approved").first()
        if sms and M.verify_payment_tolerance(totals["converted"], str(sms.amount),
                                              brand.payment_tolerance or "0"):
            sms.status = "used"
            sms.save(update_fields=["status", "updated_date"])
            txn.processing_fee = totals["fee"]
            txn.discount_amount = totals["discount"]
            txn.local_net_amount = totals["converted"]
            txn.local_currency = totals["currency"]
            txn.gateway_id = gateway_id
            txn.sender_key = gateway_info.get("sender_key", "")
            txn.status = "completed"
            txn.sender = sms.number
            txn.trx_id = trxid
            txn.save()
            txn.refresh_from_db()
            payload = transaction_payload(txn, name_field="name")
            queue_or_send_webhook(txn, payload)
            hooks.do_action("transactions.updated", [payload])
            return {"status": "true", "title": "Transaction Verified",
                    "message": "The Transaction ID has been successfully verified."}
        if sms is None:
            if options.get("pending_payment") == "enable":
                if not mobile_number:
                    return {"status": "false", "title": "Transaction Not Matched",
                            "message": "The Transaction ID you entered could not be verified. Please try again "
                                       "after some time, or enter your phone number and submit for manual approval.",
                            "visible_number": "true"}
                txn.processing_fee = totals["fee"]
                txn.discount_amount = totals["discount"]
                txn.local_net_amount = totals["converted"]
                txn.local_currency = totals["currency"]
                txn.gateway_id = gateway_id
                txn.sender_key = gateway_info.get("sender_key", "")
                txn.status = "pending"
                txn.sender = mobile_number
                txn.trx_id = trxid
                txn.save()
                return {"status": "true", "title": "Transaction Submitted",
                        "message": "Your Transaction ID has been successfully submitted"}
        return fail("Transaction Not Found",
                    "The Transaction ID you entered could not be verified. Please check the ID and try again after some time.")

    if gtype == "manual":
        if gateway_info.get("verify_by") == "trxid":
            if not trxid:
                return fail("Missing Transaction ID",
                            "The Transaction ID field cannot be empty. Please provide a valid Transaction ID.")
            if Transaction.objects.filter(trx_id=trxid).exists():
                return fail("Duplicate Transaction ID",
                            "This Transaction ID is already exits. Please provide a different one.")
            txn.processing_fee = totals["fee"]
            txn.discount_amount = totals["discount"]
            txn.local_net_amount = totals["converted"]
            txn.local_currency = totals["currency"]
            txn.gateway_id = gateway_id
            txn.status = "pending"
            txn.trx_id = trxid
            txn.save()
            txn.refresh_from_db()
            hooks.do_action("transactions.updated", [transaction_payload(txn, name_field="name")])
            return {"status": "true", "title": "Transaction Submitted",
                    "message": "Your Transaction ID has been successfully submitted"}
        if gateway_info.get("verify_by") == "slip":
            if slip_file is None:
                return fail("Missing Transaction Slip",
                            "The Transaction slip field cannot be empty. Please provide a valid Transaction Slip.")
            from core.storage import upload_image
            result = upload_image(slip_file, 5 * 1024 * 1024)
            if not result.get("status"):
                return fail("Missing Transaction Slip",
                            "The Transaction slip field cannot be empty. Please provide a valid Transaction Slip.")
            txn.processing_fee = totals["fee"]
            txn.discount_amount = totals["discount"]
            txn.local_net_amount = totals["converted"]
            txn.local_currency = totals["currency"]
            txn.gateway_id = gateway_id
            txn.status = "pending"
            txn.trx_slip = site_base.rstrip("/") + "/media/storage/" + result["file"]
            txn.save()
            txn.refresh_from_db()
            hooks.do_action("transactions.updated", [transaction_payload(txn, name_field="name")])
            return {"status": "true", "title": "Transaction Submitted",
                    "message": "Your Transaction ID has been successfully submitted"}
        return fail("Request Failed. Code 106", "Please fill in all required fields before proceeding.")

    return fail("Request Failed. Code 105", "Please fill in all required fields before proceeding.")


def invoice_line_totals(items) -> tuple[list[dict], dict]:
    """action-v2 invoice math: VAT is a PERCENT rate (adapter:8529-8577)."""
    lines = []
    subtotal = "0"
    total_discount = "0"
    total_vat = "0"
    for row in items:
        amount = M.money_sanitize(str(row.amount))
        quantity = M.money_sanitize(str(row.quantity))
        discount = M.money_sanitize(str(row.discount))
        vat_rate = M.money_sanitize(str(row.vat))
        gross = M.money_mul(amount, quantity)
        net = M.money_sub(gross, discount)
        vat_amount = M.money_div(M.money_mul(net, vat_rate), "100")
        line_total = M.money_add(net, vat_amount)
        lines.append({"description": row.description, "unitPrice": M.money_round(amount, 2),
                      "quantity": quantity, "discount": M.money_round(discount, 2),
                      "vat": M.money_round(vat_amount, 2), "total": M.money_round(line_total, 2)})
        subtotal = M.money_add(subtotal, gross)
        total_discount = M.money_add(total_discount, discount)
        total_vat = M.money_add(total_vat, vat_amount)
    return lines, {"subtotal": subtotal, "discount": total_discount, "vat": total_vat}


def invoice_display_totals(items) -> tuple[list[dict], dict]:
    """index.php invoice DISPLAY math (1366-1408): vat is a FLAT amount there
    (lineTotal = amount*qty - discount + vat). Theme template uses percent;
    the router page uses flat — mirrored per surface."""
    lines = []
    subtotal = "0"
    total_discount = "0"
    total_vat = "0"
    for row in items:
        amount = M.money_sanitize(str(row.amount))
        quantity = M.money_sanitize(str(row.quantity))
        discount = M.money_sanitize(str(row.discount))
        vat = M.money_sanitize(str(row.vat))
        line_total = M.money_add(M.money_sub(M.money_mul(amount, quantity), discount), vat)
        lines.append({"description": row.description, "unitPrice": M.money_round(amount, 2),
                      "quantity": quantity, "discount": M.money_round(discount, 2),
                      "vat": M.money_round(vat, 2), "total": M.money_round(line_total, 2)})
        subtotal = M.money_add(subtotal, M.money_mul(amount, quantity))
        total_discount = M.money_add(total_discount, discount)
        total_vat = M.money_add(total_vat, vat)
    return lines, {"subtotal": subtotal, "discount": total_discount, "vat": total_vat}


def create_invoice_transaction(invoice_ref: str, site_base: str) -> dict:
    """Mirror action-v2 `invoice` (adapter:8517-8598). Returns {ok, redirect|title,message}."""
    inv = Invoice.objects.filter(ref=invoice_ref, status="unpaid").first()
    if not inv:
        return {"ok": False, "title": "Invalid Invoice ID",
                "message": "Please fill in all required fields before proceeding."}
    items = list(InvoiceItem.objects.filter(invoice_id=inv.ref, brand_id=inv.brand_id))
    _, sums = invoice_line_totals(items)
    try:
        customer = json.loads(inv.customer_info or "{}")
    except ValueError:
        customer = {}
    amount = M.money_add(M.money_add(M.money_sub(sums["subtotal"], sums["discount"]), sums["vat"]),
                         M.money_sanitize(inv.shipping or "0"))
    base = site_base.rstrip("/")
    pp_id = M.generate_item_id(27, 27)
    Transaction.objects.create(
        brand_id=inv.brand_id, source="invoice", ref=pp_id,
        customer_info=json.dumps({"name": customer.get("name", ""), "email": customer.get("email", ""),
                                  "mobile": customer.get("mobile", "")}),
        amount=M.money_sanitize(amount), currency=inv.currency,
        source_info=json.dumps([{"label": "Invoice Id", "value": invoice_ref}]),
        metadata=json.dumps({"invoice_id": invoice_ref}),
        return_url=f"{base}/invoice/{invoice_ref}", webhook_url=f"{base}/invoice/webhook")
    return {"ok": True, "redirect": f"{base}/payment/{pp_id}"}


def handle_invoice_webhook(data: dict) -> tuple[int, str]:
    """Mirror index.php:1195-1290 invoice/webhook receiver. Exact hook shape:
    customer_info RAW string, due_date from the (nonexistent) expired_date
    column (-> None), shipping rounded, updated_date = now, items [] (the
    items query filters a nonexistent `ref` column). Always 200 OK on shape,
    400 only for empty/bad JSON with plain-text bodies."""
    if not data:
        return 400, "No payload received"
    pp_id = data.get("pp_id", "")
    txn = Transaction.objects.filter(ref=pp_id).first()
    if txn:
        try:
            metadata = json.loads(txn.metadata or "{}")
        except ValueError:
            metadata = {}
        invoice_id = metadata.get("invoice_id", "") if isinstance(metadata, dict) else ""
        inv = Invoice.objects.filter(ref=invoice_id).first() if invoice_id else None
        if inv:
            if txn.status == "completed":
                inv.gateway_id = txn.gateway_id
                inv.status = "paid"
                inv.save()
            elif txn.status == "refunded":
                inv.gateway_id = txn.gateway_id
                inv.status = "refunded"
                inv.save()
            inv.refresh_from_db()
            brand = Brand.objects.filter(brand_id=inv.brand_id).first()
            tz = norm_tz(brand.timezone if brand else "")
            hooks.do_action("invoices.updated.status", [{
                "customer_info": inv.customer_info or "{}",
                "invoice_info": {
                    "invoice_id": inv.ref, "brand_id": inv.brand_id, "currency": inv.currency,
                    "due_date": None,
                    "shipping": M.money_round(M.money_sanitize(inv.shipping or "0")),
                    "status": inv.status, "note": inv.note, "private_note": inv.private_note,
                    "created_date": M.convert_utc_to_user_tz(inv.created_date, tz, "%b %d, %Y %I:%M %p"),
                    "updated_date": M.convert_utc_to_user_tz(M.get_current_datetime(), tz,
                                                             "%b %d, %Y %I:%M %p")},
                "invoice_items": []}])
    return 200, "OK"


def check_transaction(pp_id: str = ""):
    """Mirror pp_check_transaction(): True/False existence by ref."""
    return Transaction.objects.filter(ref=pp_id).exists()


def check_transaction_id(trx_id: str = ""):
    return Transaction.objects.filter(trx_id=trx_id).exists()
