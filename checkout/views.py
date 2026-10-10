"""
Checkout surfaces — port of index.php cases $path_payment / $path_invoice /
$path_payment_link, rendered through the twenty-six theme.

- payment/<pp_id>            checkout (gateway list -> gateway page -> verify)
- payment/<pp_id>/callback   API-gateway return URL (pp_callback_url)
- invoice/<ref>              invoice page
- payment-link/<ref>[/brand] payment link + default link submission
"""
import json

from django.http import HttpResponse
from django.shortcuts import redirect, render

from core import money as M
from core.models import Brand, Gateway, GatewayParameter, Invoice, InvoiceItem, PaymentLink, SmsData, Transaction
from core.transactions import gateway_options, set_transaction_status, transaction_payload
from gateways.registry import get_gateway


def _brand_context(brand: Brand) -> dict:
    return {
        "id": brand.brand_id, "name": brand.name if brand.name != "--" else brand.identify_name,
        "identifyName": brand.identify_name, "logo": None if brand.logo == "--" else brand.logo,
        "favicon": None if brand.favicon == "--" else brand.favicon,
        "support": {"email": brand.support_email_address, "phone": brand.support_phone_number,
                    "website": brand.support_website, "whatsapp": brand.whatsapp_number,
                    "telegram": f"https://t.me/{brand.telegram}",
                    "messenger": f"https://m.me/{brand.facebook_messenger}",
                    "fb_page": f"https://facebook.com/{brand.facebook_page}"},
        "locale": {"timezone": brand.timezone, "language": brand.language, "currency": brand.currency_code},
    }


def _gateway_context(gw: Gateway) -> dict:
    return {
        "gateway_id": gw.gateway_id, "slug": gw.slug, "name": gw.name, "display": gw.display,
        "logo": gw.logo, "currency": gw.currency,
        "min_allow": M.money_round(str(gw.min_allow)), "max_allow": M.money_round(str(gw.max_allow)),
        "fixed_discount": M.money_round(str(gw.fixed_discount)),
        "percentage_discount": M.money_round(str(gw.percentage_discount)),
        "fixed_charge": M.money_round(str(gw.fixed_charge)),
        "percentage_charge": M.money_round(str(gw.percentage_charge)),
        "primary_color": gw.primary_color, "text_color": gw.text_color,
        "btn_color": gw.btn_color, "btn_text_color": gw.btn_text_color,
        "options": gateway_options(gw.gateway_id, gw.brand_id),
    }


def checkout_payment(request, pp_id: str):
    txn = Transaction.objects.filter(ref=pp_id).first()
    if not txn:
        return render(request, "core/404.html", status=404)
    brand = Brand.objects.filter(brand_id=txn.brand_id).first()
    if not txn.status == "initiated" and txn.status not in ("initiated", "completed", "pending", "refunded", "canceled"):
        return render(request, "core/404.html", status=404)

    if txn.status != "initiated":
        return render(request, "checkout/status.html",
                      {"transaction": transaction_payload(txn), "brand": _brand_context(brand)})

    gateways = [ _gateway_context(g) for g in Gateway.objects.filter(brand_id=txn.brand_id, status="active") ]
    selected_slug = request.GET.get("gateway")

    if request.method == "POST":
        # Shopper submits sender + trx_id for automation/manual gateways -> pending
        sender = (request.POST.get("sender") or "").strip()
        trx_id = (request.POST.get("trx_id") or "").strip().upper()
        gateway_id = request.POST.get("gateway_id") or ""
        gw = Gateway.objects.filter(gateway_id=gateway_id, brand_id=txn.brand_id).first()
        if gw and sender and trx_id:
            plugin = get_gateway(gw.slug)
            gtype = (plugin.info().get("gateway_type") if plugin else "automation")
            if gtype in ("automation", "manual"):
                txn.sender = sender
                txn.trx_id = trx_id
                txn.gateway_id = gateway_id
                txn.sender_key = plugin.info().get("sender_key", "") if plugin else ""
                txn.sender_type = plugin.info().get("sender_type", "Personal") if plugin else "Personal"
                txn.status = "pending"
                txn.save()
                # Immediate match attempt against approved sms_data (cron reconciles the rest)
                sms = SmsData.objects.filter(sender_key=txn.sender_key, type=txn.sender_type,
                                             trx_id=trx_id, status="approved").first()
                if sms and M.verify_payment_tolerance(str(txn.local_net_amount or txn.amount),
                                                      str(sms.amount),
                                                      brand.payment_tolerance if brand else "0"):
                    sms.status = "used"
                    sms.save(update_fields=["status", "updated_date"])
                    txn.status = "completed"
                    txn.sender = sms.number
                    txn.save()
                return redirect(f"/payment/{pp_id}/")
        return redirect(f"/payment/{pp_id}/")

    if selected_slug:
        gw = Gateway.objects.filter(brand_id=txn.brand_id, slug=selected_slug, status="active").first()
        if not gw:
            return render(request, "core/404.html", status=404)
        plugin = get_gateway(gw.slug)
        ctx = {"transaction": transaction_payload(txn), "brand": _brand_context(brand),
               "gateway": _gateway_context(gw),
               "instructions": plugin.instructions({"options": gateway_options(gw.gateway_id, gw.brand_id),
                                                    "transaction": transaction_payload(txn)}) if plugin else []}
        # API gateways start payment immediately (process_payment redirect)
        if plugin and plugin.info().get("gateway_type") == "api":
            result = plugin.process_payment({
                "transaction": transaction_payload(txn),
                "options": gateway_options(gw.gateway_id, gw.brand_id),
                "callback_url": request.build_absolute_uri(f"/payment/{pp_id}/callback/?gateway={gw.gateway_id}"),
                "cancel_url": request.build_absolute_uri(f"/payment/{pp_id}/"),
            })
            if result.get("action") == "redirect":
                return redirect(result["url"])
            ctx["gateway_html"] = result.get("html", "")
        return render(request, "checkout/gateway.html", ctx)

    return render(request, "checkout/checkout.html",
                  {"transaction": transaction_payload(txn), "brand": _brand_context(brand), "gateways": gateways})


def checkout_callback(request, pp_id: str):
    """pp_callback_url() target — verifies API gateway return and completes."""
    txn = Transaction.objects.filter(ref=pp_id).first()
    if not txn:
        return render(request, "core/404.html", status=404)
    gateway_id = request.GET.get("gateway") or txn.gateway_id
    gw = Gateway.objects.filter(gateway_id=gateway_id).first()
    plugin = get_gateway(gw.slug) if gw else None
    if plugin and plugin.info().get("gateway_type") == "api":
        result = plugin.callback({
            "transaction": transaction_payload(txn),
            "options": gateway_options(gw.gateway_id, gw.brand_id),
            "query": dict(request.GET), "post": dict(request.POST),
        })
        if result.get("verified"):
            set_transaction_status(pp_id, "completed", gateway_id,
                                   result.get("trx_id", ""), result.get("source_info", []))
    return redirect(f"/payment/{pp_id}/")


def invoice_page(request, ref: str):
    inv = Invoice.objects.filter(ref=ref).first()
    if not inv:
        return render(request, "core/404.html", status=404)
    brand = Brand.objects.filter(brand_id=inv.brand_id).first()
    items = list(InvoiceItem.objects.filter(invoice_id=ref))
    try:
        customer = json.loads(inv.customer_info or "{}")
    except ValueError:
        customer = {}
    subtotal = "0"
    for it in items:
        line = M.money_mul(str(it.amount), str(it.quantity or 1))
        line = M.money_sub(line, str(it.discount or 0))
        line = M.money_add(line, str(it.vat or 0))
        subtotal = M.money_add(subtotal, line)
    total = M.money_add(subtotal, inv.shipping or "0")
    return render(request, "checkout/invoice.html",
                  {"invoice": inv, "items": items, "customer": customer,
                   "brand": _brand_context(brand) if brand else {},
                   "subtotal": M.money_round(subtotal), "total": M.money_round(total)})


def payment_link_page(request, ref: str, brand_id: str | None = None):
    link = PaymentLink.objects.filter(ref=ref).first()
    if not link or link.status != "active":
        return render(request, "checkout/link_inactive.html", status=404)
    brand = Brand.objects.filter(brand_id=link.brand_id).first()
    try:
        product = json.loads(link.product_info or "{}")
    except ValueError:
        product = {}
    if request.method == "POST":
        from core.models import Customer
        from core.money import generate_item_id
        name = (request.POST.get("full_name") or "").strip()
        email = (request.POST.get("email_address") or "").strip()
        mobile = (request.POST.get("mobile_number") or "").strip()
        if name and email and mobile:
            pp_id = generate_item_id(27, 27)
            Transaction.objects.create(
                brand_id=link.brand_id, source="payment-link-default", ref=pp_id,
                customer_info=json.dumps({"name": name, "email": email, "mobile": mobile}),
                amount=link.amount, currency=link.currency, metadata="{}",
                return_url="--", webhook_url="--")
            if not Customer.objects.filter(brand_id=link.brand_id, email=email).exists():
                Customer.objects.create(ref=generate_item_id(), brand_id=link.brand_id,
                                        name=name, email=email, mobile=mobile, inserted_via="checkout")
            return redirect(f"/payment/{pp_id}/")
    return render(request, "checkout/payment_link.html",
                  {"link": link, "product": product, "brand": _brand_context(brand) if brand else {}})


def receipt_pdf(request, pp_id: str):
    """pp_downloadReceiptPDF() — simple printable receipt (PDF via print CSS)."""
    txn = Transaction.objects.filter(ref=pp_id).first()
    if not txn:
        return render(request, "core/404.html", status=404)
    brand = Brand.objects.filter(brand_id=txn.brand_id).first()
    resp = render(request, "checkout/receipt.html",
                  {"transaction": transaction_payload(txn), "brand": _brand_context(brand) if brand else {}})
    resp["Content-Disposition"] = f'inline; filename="receipt-{pp_id}.html"'
    return resp
