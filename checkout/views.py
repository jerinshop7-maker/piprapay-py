"""
Checkout surfaces — port of index.php cases $path_payment / $path_invoice /
$path_payment_link + pp_gateway_render()/pp_gateways() (pp-functions.php) +
the twenty-six theme templates.

Conventions mirrored exactly:
- ?gateway=<gateway_id> (gateway_id, not slug).
- ?lang=<code> stores session ui_language (pp_set_lang).
- ?cancel marks the transaction canceled.
- API gateways: ?pp_callback present -> callback(), else process_payment().
- Transaction verify is a JSON endpoint (same-URL fetch in PHP) accepting
  gateway-id/transaction-id/trxid/mobile_number/slip with Code 101-106 shapes.
"""
import json

from django.http import JsonResponse
from django.shortcuts import redirect, render

from core import money as M
from core.env import get_env
from core.models import (Brand, Currency, Faq, Gateway, Invoice, InvoiceItem, PaymentLink,
                         PaymentLinkField, Transaction)
from core.storage import upload_image
from core.transactions import (build_return_url, compute_gateway_totals, create_invoice_transaction,
                               gateway_options, handle_invoice_webhook, invoice_line_totals,
                               set_transaction_status, transaction_payload, verify_transaction_post)
from gateways.registry import get_gateway
from themes.twenty_six import get_theme


# ---------------------------------------------------------------- helpers

def _theme_lang(request, brand: Brand):
    theme = get_theme(brand.theme if brand else "twenty-six")
    if request.GET.get("lang") not in (None, ""):
        request.session["ui_language"] = re_sub(request.GET["lang"])
        request._pp_lang_redirect = True
    session_lang = request.session.get("ui_language", "")
    supported = theme.supported_languages()
    if session_lang in supported:
        language = session_lang
    elif (brand.language if brand else "") in supported:
        language = brand.language
    else:
        language = "en" if "en" in supported else next(iter(supported), "en")
    return theme, language, theme.build_lang(language)


def _maybe_lang_redirect(request):
    """PHP clears ?lang= via JS redirect; server-side we redirect preserving
    the other query params (gateway.php keeps ?gateway=)."""
    if getattr(request, "_pp_lang_redirect", False):
        qs = request.GET.copy()
        qs.pop("lang", None)
        suffix = ("?" + qs.urlencode()) if qs else ""
        return redirect(request.path + suffix)
    return None


def re_sub(lang: str) -> str:
    import re as _re
    return _re.sub(r"[^a-z]", "", (lang or "").lower())


def _brand_info(brand: Brand, *, invoice_shape: bool = False, language: str = "") -> dict:
    """index.php brandInfo. Payment shape falls back only on '--' (empty leaks,
    like PHP); locale.language is the resolved language."""
    name = brand.name if brand.name != "--" else brand.identify_name
    locale_lang = language or brand.language
    if invoice_shape:
        return {
            "id": brand.brand_id, "name": name, "identifyName": brand.identify_name,
            "logo": None if brand.logo in ("--", "", None) else brand.logo,
            "favicon": None if brand.favicon in ("--", "", None) else brand.favicon,
            "support": {"email": brand.support_email_address, "phone": brand.support_phone_number,
                        "website": brand.support_website, "whatsapp": brand.whatsapp_number,
                        "telegram": brand.telegram},
            "address": {"street": brand.street_address, "city": brand.city_town,
                        "postal": brand.postal_code, "country": brand.country},
            "locale": {"timezone": brand.timezone, "language": locale_lang,
                       "currency": brand.currency_code},
        }
    return {
        "id": brand.brand_id, "name": name, "identifyName": brand.identify_name,
        "logo": ("https://help.piprapay.com/storage/branding_media/8a5c6ee4-8eba-401d-bffb-c43006d5f65d.png"
                 if brand.logo == "--" else brand.logo),
        "favicon": ("https://help.piprapay.com/favicon/icon-144x144.png"
                    if brand.favicon == "--" else brand.favicon),
        "support": {"email": brand.support_email_address, "phone": brand.support_phone_number,
                    "website": brand.support_website, "whatsapp": brand.whatsapp_number,
                    "telegram": f"https://t.me/{brand.telegram}",
                    "messenger": f"https://m.me/{brand.facebook_messenger}",
                    "fb_page": f"https://facebook.com/{brand.facebook_page}"},
        "address": {"street": brand.street_address, "city": brand.city_town,
                    "postal": brand.postal_code, "country": brand.country},
        "locale": {"timezone": brand.timezone, "language": brand.language,
                   "currency": brand.currency_code},
    }


def _theme_options(brand: Brand) -> dict:
    # Option keys are `<theme-slug>-<field>` (index.php:1062), brand-scoped.
    slug = (brand.theme if brand and brand.theme else "twenty-six") or "twenty-six"
    theme = get_theme(slug)
    return {f["name"]: get_env(f"{slug}-{f['name']}", brand.brand_id if brand else "both")
            for f in theme.fields()}


def _gateway_list(brand: Brand, txn: Transaction, tab: str) -> list:
    """Mirror pp_gateways(): FX convert + first-row fee quirk + min/max gating."""
    gateways = list(Gateway.objects.filter(tab=tab, brand_id=brand.brand_id, status="active"))
    if not gateways:
        return []
    rates = {c.code: M.money_sanitize(str(c.rate)) for c in Currency.objects.filter(brand_id=brand.brand_id)}
    # NOTE PHP quirk: fixed/percentage fee+discount always come from row [0].
    first = gateways[0]
    out = []
    for row in gateways:
        txn_amount = M.money_sanitize(str(txn.amount))
        if txn.currency == row.currency:
            converted = txn_amount
        elif row.currency in rates:
            converted = M.money_div(txn_amount, rates[row.currency])
        else:
            converted = "0"
        pct_disc = M.money_div(M.money_mul(converted, M.money_sanitize(str(first.percentage_discount)), 8), "100", 8)
        total_discount = M.money_add(M.money_sanitize(str(first.fixed_discount)), pct_disc, 8)
        pct_fee = M.money_div(M.money_mul(converted, M.money_sanitize(str(first.percentage_charge)), 8), "100", 8)
        total_fee = M.money_add(M.money_sanitize(str(first.fixed_charge)), pct_fee, 8)
        converted = M.money_add(M.money_sub(converted, total_discount, 8), total_fee, 8)
        min_allow, max_allow = M.money_sanitize(str(row.min_allow)), M.money_sanitize(str(row.max_allow))
        try:
            from decimal import Decimal
            has_no_max = Decimal(max_allow or "0") <= 0 or max_allow in ("", "--")
            above_min = Decimal(M.money_round(converted)) >= Decimal(min_allow or "0")
            below_max = True if has_no_max else Decimal(M.money_round(converted)) <= Decimal(max_allow)
        except Exception:
            above_min, below_max = True, True
        if above_min and below_max:
            out.append({
                "gateway_id": row.gateway_id, "slug": row.slug, "name": row.name, "display": row.display,
                "logo": row.logo, "currency": row.currency,
                "min_allow": M.money_round(str(row.min_allow)), "max_allow": M.money_round(str(row.max_allow)),
                "fixed_discount": M.money_round(str(row.fixed_discount)),
                "percentage_discount": M.money_round(str(row.percentage_discount)),
                "fixed_charge": M.money_round(str(row.fixed_charge)),
                "percentage_charge": M.money_round(str(row.percentage_charge)),
                "primary_color": row.primary_color, "text_color": row.text_color,
                "btn_color": row.btn_color, "btn_text_color": row.btn_text_color,
                "converted": M.money_round(converted),
            })
    return out


def _transaction_info(txn: Transaction, brand: Brand, gateway_display: str = "") -> dict:
    try:
        customer = json.loads(txn.customer_info or "{}")
    except ValueError:
        customer = {}
    return {
        "ref": txn.ref,
        "customer": {"id": None, "name": customer.get("name"), "email": customer.get("email"),
                     "mobile": customer.get("mobile")},
        "payment_method": gateway_display,
        "currency": txn.currency, "amount": M.money_round(str(txn.amount)),
        "discount_amount": M.money_round(str(txn.discount_amount)),
        "processing_fee": M.money_round(str(txn.processing_fee)),
        "local_net_amount": M.money_round(str(txn.local_net_amount)),
        "local_currency": txn.local_currency,
        "return_url": build_return_url(txn.return_url or "", txn.status, txn.ref),
        "created_date": txn.created_date.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_date": txn.updated_date.strftime("%Y-%m-%d %H:%M:%S"),
        "status": txn.status, "brandId": brand.brand_id,
    }


def _resolve_steps(plugin, language: str, instructions: list) -> list:
    """Resolve instruction lang keys (buildLangArray) + var substitution with
    <span class="dynamic-value"> wrappers (pp_gateway_render rendering)."""
    lang_map = {}
    if plugin:
        for k, v in plugin.lang_text().items():
            lang_map[k] = v.get(language, v.get("en", k))
    steps = []
    for i, s in enumerate(instructions, start=1):
        text = lang_map.get(str(s.get("text", "")), str(s.get("text", "")))
        for k, v in (s.get("vars") or {}).items():
            text = text.replace(k, f'<span class="dynamic-value">{v}</span>')
        step = dict(s)
        step["label"] = text
        step["text"] = text
        step["row"] = i
        steps.append(step)
    return steps


# ---------------------------------------------------------------- payment

def checkout_payment(request, pp_id: str):
    txn = Transaction.objects.filter(ref=pp_id).first()
    if not txn:
        return render(request, "core/404.html", status=404)
    brand = Brand.objects.filter(brand_id=txn.brand_id).first()
    if not brand:
        return render(request, "core/404.html", status=404)
    theme, language, lang = _theme_lang(request, brand)
    redir = _maybe_lang_redirect(request)
    if redir:
        return redir

    if "cancel" in request.GET and txn.status == "initiated":
        set_transaction_status(pp_id, "canceled")
        return redirect(f"/payment/{pp_id}/")

    if txn.status != "initiated" and "receipt" not in request.GET:
        payload = transaction_payload(txn)
        payload["return_url"] = build_return_url(txn.return_url or "", txn.status, txn.ref)
        return render(request, "checkout/status.html",
                      {"transaction": payload, "brand": _brand_info(brand, language=language),
                       "lang": lang, "language": language, "theme": theme,
                       "options": _theme_options(brand)})

    if "receipt" in request.GET and txn.status != "initiated":
        return receipt_pdf(request, pp_id)

    gateway_id = request.GET.get("gateway", "")
    if gateway_id:
        gw = Gateway.objects.filter(gateway_id=gateway_id, brand_id=brand.brand_id,
                                    status="active").first()
        if not gw:
            from django.http import HttpResponse
            return HttpResponse("Direct access not allowed", status=403)
        plugin = get_gateway(gw.slug)
        options = gateway_options(gw.gateway_id, brand.brand_id)
        totals = compute_gateway_totals(str(txn.amount), txn.currency, gw, brand.brand_id)
        txn_ctx = dict(transaction_payload(txn),
                       local_net_amount=M.money_round(totals["converted"]),
                       local_currency=totals["currency"])
        gtype = plugin.info().get("gateway_type") if plugin else None
        if plugin is None and gw.tab == "bank":
            # Bank fallback (pp_gateway_render): manual/slip + bank_step_* lang.
            gtype = "manual"
        plugin_lang = {}
        if plugin:
            for k, v in plugin.lang_text().items():
                plugin_lang[k] = v.get(language, v.get("en", ""))
        from gateways.render_lang import framework_built, resolve_bank_steps
        fw = framework_built(language)
        plugin_lang.update({k: v for k, v in fw.items() if k not in plugin_lang})
        if plugin is None and gw.tab == "bank":
            from gateways.render_lang import BANK_LANG_TEXT
            for k, v in BANK_LANG_TEXT.items():
                plugin_lang[k] = v.get(language, v.get("en", k))
            instructions = resolve_bank_steps(options, txn_ctx, language)
            steps = instructions
        else:
            instructions = plugin.instructions({"options": options, "transaction": txn_ctx}) if plugin else []
            steps = _resolve_steps(plugin, language, instructions)
        base = request.build_absolute_uri("/").rstrip("/")
        callback_url = (f"{base}/payment/{pp_id}/?gateway={gw.gateway_id}&pp_callback=1"
                        f"&gid={gw.gateway_id}")
        gateway_ctx = {"gateway_id": gw.gateway_id, "slug": gw.slug, "name": gw.name,
                       "display": gw.display, "logo": gw.logo, "currency": gw.currency,
                       "min_allow": M.money_round(str(gw.min_allow)),
                       "max_allow": M.money_round(str(gw.max_allow)),
                       "fixed_discount": M.money_round(str(gw.fixed_discount)),
                       "percentage_discount": M.money_round(str(gw.percentage_discount)),
                       "fixed_charge": M.money_round(str(gw.fixed_charge)),
                       "percentage_charge": M.money_round(str(gw.percentage_charge)),
                       "primary_color": gw.primary_color, "text_color": gw.text_color,
                       "btn_color": gw.btn_color, "btn_text_color": gw.btn_text_color,
                       "options": options, "local_net_amount": txn_ctx["local_net_amount"],
                       "local_currency": txn_ctx["local_currency"]}
        supported_langs = plugin.supported_languages() if plugin else {}
        if plugin and gtype == "api":
            if "pp_callback" in request.GET:
                result = plugin.callback({"transaction": txn_ctx, "options": options,
                                          "query": request.GET.dict(), "post": request.POST.dict(),
                                          "callback_url": callback_url,
                                          "cancel_url": f"{base}/payment/{pp_id}/"})
                if result.get("complete_now") or result.get("verified"):
                    set_transaction_status(pp_id, "completed", gw.gateway_id,
                                           result.get("trx_id", ""), result.get("source_info", []))
                    return redirect(f"/payment/{pp_id}/")
                # PHP leaves cancel/fail at `initiated` (retry preserved):
                # redirect back without forcing `canceled`.
                if result.get("canceled"):
                    return redirect(f"/payment/{pp_id}/")
                if result.get("pending"):
                    return render(request, "checkout/gateway.html",
                                  {"transaction": txn_ctx, "brand": _brand_info(brand, language=language), "gateway": gateway_ctx,
                                   "instructions": steps, "plugin_lang": plugin_lang, "lang": lang,
                                   "language": language, "supported_langs": supported_langs,
                                   "gateway_html": "<center>Payment is pending confirmation on the network.</center>"})
                return render(request, "checkout/gateway.html",
                              {"transaction": txn_ctx, "brand": _brand_info(brand, language=language), "gateway": gateway_ctx,
                               "instructions": steps, "plugin_lang": plugin_lang, "lang": lang,
                               "language": language, "supported_langs": supported_langs,
                               "gateway_html": result.get("html", result.get("action", ""))})
            _brand = Brand.objects.filter(brand_id=txn.brand_id).first()
            result = plugin.process_payment({"transaction": txn_ctx, "options": options,
                                             "query": request.GET.dict(), "post": request.POST.dict(),
                                             "lang": plugin_lang,
                                             "brand": {"id": txn.brand_id,
                                                       "currency": _brand.currency_code if _brand else "BDT",
                                                       "timezone": _brand.timezone if _brand else "Asia/Dhaka"},
                                             "gateway": {"gateway_id": gw.gateway_id, "slug": gw.slug,
                                                         "currency": gw.currency},
                                             "client_ip": request.META.get("REMOTE_ADDR", ""),
                                             "callback_url": callback_url,
                                             "cancel_url": f"{base}/payment/{pp_id}/",
                                             "ipn_url": f"{base}/ipn/{gw.gateway_id}"})
            if isinstance(result, dict) and result.get("verified") and result.get("complete_now"):
                set_transaction_status(pp_id, "completed", gw.gateway_id,
                                       result.get("trx_id", ""), result.get("source_info", []))
                return redirect(f"/payment/{pp_id}/")
            if result.get("action") == "redirect":
                return redirect(result["url"])
            return render(request, "checkout/gateway.html",
                          {"transaction": txn_ctx, "brand": _brand_info(brand, language=language), "gateway": gateway_ctx,
                           "instructions": steps, "plugin_lang": plugin_lang, "lang": lang,
                           "language": language, "gateway_html": result.get("html", "")})
        _verify_by = "slip" if (plugin is None and gw.tab == "bank") else \
            (plugin.info().get("verify_by", "trxid") if plugin else "trxid")
        return render(request, "checkout/gateway.html",
                      {"transaction": txn_ctx, "brand": _brand_info(brand, language=language), "gateway": gateway_ctx,
                       "gateway_type": gtype or "automation",
                       "verify_by": _verify_by,
                       "instructions": steps, "plugin_lang": plugin_lang, "lang": lang,
                       "language": language, "supported_langs": supported_langs})

    gw_mfs = _gateway_list(brand, txn, "mfs")
    gw_bank = _gateway_list(brand, txn, "bank")
    gw_global = _gateway_list(brand, txn, "global")
    gw = Gateway.objects.filter(gateway_id=txn.gateway_id, brand_id=brand.brand_id).first()
    faqs = [{"title": f.title, "description": f.description}
            for f in Faq.objects.filter(brand_id=brand.brand_id, status="active").order_by("-id")]
    return render(request, "checkout/checkout.html",
                  {"transaction": _transaction_info(txn, brand, gw.display if gw else ""),
                   "brand": _brand_info(brand, language=language), "faqs": faqs, "options": _theme_options(brand),
                   "lang": lang, "language": language,
                   "gateways_mfs": gw_mfs, "gateways_bank": gw_bank, "gateways_global": gw_global})


def verify_endpoint(request, pp_id: str):
    """Same-URL JSON verify (PHP action-v2 transaction-verify)."""
    if request.method != "POST":
        return JsonResponse({"status": "false", "title": "Request Failed",
                             "message": "Invalid request"})
    base = request.build_absolute_uri("/").rstrip("/")
    result = verify_transaction_post(
        gateway_id=request.POST.get("gateway-id", "") or request.POST.get("gateway_id", ""),
        transaction_ref=request.POST.get("transaction-id", "") or request.POST.get("transaction_id", "") or pp_id,
        trxid=(request.POST.get("trxid", "") or "").strip(),
        mobile_number=(request.POST.get("mobile_number", "") or "").strip(),
        slip_file=request.FILES.get("slip"),
        site_base=base)
    return JsonResponse(result)


def checkout_callback(request, pp_id: str):
    """Legacy /payment/<id>/callback/ alias -> gateway page with pp_callback."""
    gid = request.GET.get("gateway", "") or request.GET.get("gid", "")
    qs = request.GET.copy()
    qs["pp_callback"] = "1"
    if gid and "gateway" not in qs:
        qs["gateway"] = gid
    return redirect(f"/payment/{pp_id}/?{qs.urlencode()}")


# ---------------------------------------------------------------- invoice

def invoice_page(request, ref: str):
    if ref == "webhook":
        from django.http import HttpResponse
        try:
            raw = request.body.decode() or ""
        except UnicodeDecodeError:
            raw = ""
        if not raw:
            return HttpResponse("No payload received", status=400, content_type="text/plain")
        try:
            data = json.loads(raw)
        except ValueError:
            return HttpResponse("Invalid JSON", status=400, content_type="text/plain")
        code, body = handle_invoice_webhook(data if isinstance(data, dict) else None)
        return HttpResponse(body, status=code, content_type="text/plain")
    inv = Invoice.objects.filter(ref=ref).first()
    if not inv:
        return render(request, "core/404.html", status=404)
    brand = Brand.objects.filter(brand_id=inv.brand_id).first()
    if not brand:
        return render(request, "core/404.html", status=404)
    theme, language, lang = _theme_lang(request, brand)
    redir = _maybe_lang_redirect(request)
    if redir:
        return redir
    try:
        customer = json.loads(inv.customer_info or "{}")
        if not isinstance(customer, dict):
            customer = {}
    except ValueError:
        customer = {}
    customer = {"id": customer.get("id"), "name": customer.get("name"),
                "email": customer.get("email"), "mobile": customer.get("mobile")}
    gw = Gateway.objects.filter(gateway_id=inv.gateway_id).first()
    items = list(InvoiceItem.objects.filter(invoice_id=inv.ref, brand_id=inv.brand_id))
    # Display page math treats vat as FLAT (index.php:1366-1408).
    from core.transactions import invoice_display_totals
    lines, sums = invoice_display_totals(items)
    shipping = M.money_sanitize(inv.shipping or "0")
    grand = M.money_add(M.money_add(M.money_sub(sums["subtotal"], sums["discount"]), sums["vat"]), shipping)
    brand_tz = (brand.timezone or "Asia/Dhaka") if brand.timezone != "--" else "Asia/Dhaka"
    due = None
    if inv.due_date not in ("--", None):
        due = M.convert_utc_to_user_tz(inv.due_date, brand_tz, "%b %d, %Y")
    ctx_invoice = {
        "iid": inv.ref, "gateway": gw.display if gw else "", "status": inv.status,
        "currency": inv.currency, "due_date": due, "shippingFee": M.money_round(shipping),
        "note": None if inv.note == "--" else inv.note,
        "privateNote": None if inv.private_note == "--" else inv.private_note,
        "created_date": M.convert_utc_to_user_tz(inv.created_date, brand_tz, "%b %d, %Y"),
        "updated_date": M.convert_utc_to_user_tz(inv.updated_date, brand_tz, "%b %d, %Y"),
        "customer": customer, "brandId": brand.brand_id,
    }
    return render(request, "checkout/invoice.html",
                  {"invoice": ctx_invoice, "items": lines,
                   "subtotal": M.money_round(sums["subtotal"]), "totalDiscount": M.money_round(sums["discount"]),
                   "totalVat": M.money_round(sums["vat"]), "grandTotal": M.money_round(grand),
                   "brand": _brand_info(brand, invoice_shape=True, language=language),
                   "lang": lang, "language": language, "options": _theme_options(brand)})


def invoice_pay(request, ref: str):
    """action-v2 `invoice` -> {status, redirect} JSON."""
    if request.method != "POST":
        return JsonResponse({"status": "false", "title": "Request Failed", "message": "Invalid request"})
    base = request.build_absolute_uri("/").rstrip("/")
    itemid = request.POST.get("itemid", "") or ref
    if not itemid:
        return JsonResponse({"status": "false", "title": "Incomplete Information",
                             "message": "Please fill in all required fields before proceeding."})
    result = create_invoice_transaction(itemid, base)
    if result.get("ok"):
        return JsonResponse({"status": "true", "redirect": result["redirect"]})
    return JsonResponse({"status": "false", "title": result.get("title", "Request Failed"),
                         "message": result.get("message", "")})


# ---------------------------------------------------------------- payment links

def _link_status(link: PaymentLink) -> str:
    if link.expired_date == "--":
        return link.status
    return "expired" if M.is_expired(link.expired_date) else link.status


def payment_link_page(request, ref: str, brand_id: str | None = None):
    if ref == "default":
        brand = Brand.objects.filter(brand_id=brand_id).first()
        if not brand:
            return render(request, "core/404.html", status=404)
        theme, language, lang = _theme_lang(request, brand)
        currency = get_env("payment-link-default-currency", brand.brand_id)
        if currency in ("--", "", None):
            currency = brand.currency_code
        return render(request, "checkout/payment_link_default.html",
                      {"brand": _brand_info(brand, invoice_shape=True, language=language), "lang": lang, "language": language,
                       "currency": currency, "brandId": brand.brand_id, "options": _theme_options(brand)})
    link = PaymentLink.objects.filter(ref=ref).first()
    if not link:
        return render(request, "core/404.html", status=404)
    brand = Brand.objects.filter(brand_id=link.brand_id).first()
    if not brand:
        return render(request, "core/404.html", status=404)
    theme, language, lang = _theme_lang(request, brand)
    redir = _maybe_lang_redirect(request)
    if redir:
        return redir
    try:
        product = json.loads(link.product_info or "{}")
        if not isinstance(product, dict):
            product = {}
    except ValueError:
        product = {}
    fields = []
    for row in PaymentLinkField.objects.filter(paymentLinkID=link.ref):
        opts = []
        accept = ""
        if row.formType in ("select", "file", "checkbox", "radio") and row.value != "--":
            opts = [o.strip() for o in row.value.split(",")]
        if row.formType == "file" and row.value != "--":
            accept = ",".join(f".{e.strip()}" for e in row.value.split(",") if e.strip())
        import re as _re
        fields.append({"type": row.formType,
                       "name": _re.sub(r"[^a-z0-9_]", "_", row.fieldName.lower()),
                       "label": row.fieldName, "options": opts, "required": row.required,
                       "accept": accept})
    tz = (brand.timezone or "Asia/Dhaka") if brand.timezone != "--" else "Asia/Dhaka"
    return render(request, "checkout/payment_link.html",
                  {"paymentLink": {"pid": link.ref, "status": _link_status(link), "currency": link.currency,
                                   "total": M.money_round(str(link.amount)),
                                   "quantity": M.money_sanitize(str(link.quantity)),
                                   "expired_date": "--" if link.expired_date in ("", "--") else M.convert_utc_to_user_tz(link.expired_date, tz, "%b %d, %Y"),
                                   "created_date": M.convert_utc_to_user_tz(link.created_date, tz, "%b %d, %Y"),
                                   "updated_date": M.convert_utc_to_user_tz(link.updated_date, tz, "%b %d, %Y"),
                                   "product": {"title": product.get("title", "") or "Product",
                                               "description": product.get("description", None)},
                                   "brandId": link.brand_id,
                                   "fields": fields},
                   "link": {"pid": link.ref, "status": _link_status(link), "currency": link.currency,
                            "total": M.money_round(str(link.amount)),
                            "product": {"title": product.get("title", "") or "Product",
                                        "description": product.get("description", "")}},
                   "fields": fields, "brand": _brand_info(brand, invoice_shape=True, language=language),
                   "lang": lang, "language": language, "options": _theme_options(brand)})


def payment_link_submit(request, ref: str):
    """action-v2 `payment-link` -> stock decrement, expiry/active checks,
    custom fields (incl. file upload), transaction create."""
    if request.method != "POST":
        return JsonResponse({"status": "false", "title": "Request Failed", "message": "Invalid request"})
    itemid = request.POST.get("itemid", "") or ref
    if not itemid:
        return JsonResponse({"status": "false", "title": "Incomplete Information",
                             "message": "Please fill in all required fields before proceeding."})
    link = PaymentLink.objects.filter(ref=itemid).first()
    if not link:
        return JsonResponse({"status": "false", "title": "Invalid Payment Link ID",
                             "message": "Please fill in all required fields before proceeding."})
    if (link.quantity or 0) > 0:
        link.quantity -= 1
        link.save(update_fields=["quantity", "updated_date"])
    else:
        return JsonResponse({"status": "false", "title": "Product Not Available",
                             "message": "Cannot generate payment link because the product is out of stock."})
    if _link_status(link) != "active":
        return JsonResponse({"status": "false", "title": "Product Not Active",
                             "message": "This payment link cannot be generated because the product is currently inactive."})
    form_data = []
    for row in PaymentLinkField.objects.filter(paymentLinkID=link.ref):
        import re as _re
        name = _re.sub(r"[^a-z0-9_]", "_", row.fieldName.lower())
        if row.formType == "file" and name in request.FILES:
            result = upload_image(request.FILES[name], 5 * 1024 * 1024)
            if result.get("status"):
                base = request.build_absolute_uri("/").rstrip("/")
                form_data.append({"label": row.fieldName, "value": f"{base}/media/storage/{result['file']}"})
        elif row.formType == "checkbox":
            # PHP posts checkbox groups as name[] arrays.
            vals = request.POST.getlist(name) + request.POST.getlist(f"{name}[]")
            form_data.append({"label": row.fieldName, "value": ", ".join(vals)})
        elif name in request.POST or f"{name}[]" in request.POST:
            key = name if name in request.POST else f"{name}[]"
            v = request.POST.getlist(key)
            form_data.append({"label": row.fieldName,
                              "value": ", ".join(v) if len(v) > 1 else (v[0].strip() if v else "")})
    pp_id = M.generate_item_id(27, 27)
    Transaction.objects.create(
        brand_id=link.brand_id, source="payment-link", ref=pp_id,
        customer_info=json.dumps({"name": (request.POST.get("full-name") or "").strip(),
                                  "email": (request.POST.get("email-address") or "").strip(),
                                  "mobile": (request.POST.get("mobile-number") or "").strip()},
                                 ensure_ascii=False),
        amount=M.money_sanitize(str(link.amount)), currency=link.currency,
        source_info=json.dumps(form_data, ensure_ascii=False),
        metadata=json.dumps({"paymentLink_id": itemid}))
    base = request.build_absolute_uri("/").rstrip("/")
    return JsonResponse({"status": "true", "redirect": f"{base}/payment/{pp_id}"})


def payment_link_default_submit(request, brand_id: str):
    """action-v2 `payment-link-default`."""
    if request.method != "POST":
        return JsonResponse({"status": "false", "title": "Request Failed", "message": "Invalid request"})
    itemid = request.POST.get("itemid", "") or brand_id
    if not itemid:
        return JsonResponse({"status": "false", "title": "Incomplete Information",
                             "message": "Please fill in all required fields before proceeding."})
    brand = Brand.objects.filter(brand_id=itemid).first()
    if not brand:
        return JsonResponse({"status": "false", "title": "Invalid Payment Link ID",
                             "message": "Please fill in all required fields before proceeding."})
    currency = get_env("payment-link-default-currency", brand.brand_id)
    if currency in ("--", "", None):
        currency = brand.currency_code
    pp_id = M.generate_item_id(27, 27)
    Transaction.objects.create(
        brand_id=brand.brand_id, source="payment-link-default", ref=pp_id,
        customer_info=json.dumps({"name": (request.POST.get("full-name") or "").strip(),
                                  "email": (request.POST.get("email-address") or "").strip(),
                                  "mobile": (request.POST.get("mobile-number") or "").strip()},
                                 ensure_ascii=False),
        amount=M.money_sanitize(request.POST.get("amount", "0") or "0"), currency=currency,
        metadata=json.dumps({"paymentLink_id": itemid}))
    base = request.build_absolute_uri("/").rstrip("/")
    return JsonResponse({"status": "true", "redirect": f"{base}/payment/{pp_id}"})


def receipt_pdf(request, pp_id: str):
    """Printable receipt (PHP renders FPDF here; HTML documented in AGENTS.md)."""
    txn = Transaction.objects.filter(ref=pp_id).first()
    if not txn:
        return render(request, "core/404.html", status=404)
    brand = Brand.objects.filter(brand_id=txn.brand_id).first()
    payload = transaction_payload(txn)
    # PHP receipt B22: Amount Paid = amount - discount + fee, rounded.
    payload["amount_paid"] = M.money_round(str(M.money_sub(
        M.money_add(str(txn.amount), str(txn.processing_fee)), str(txn.discount_amount))))
    resp = render(request, "checkout/receipt.html",
                  {"transaction": payload,
                   "brand": _brand_info(brand, language=brand.language if brand else "") if brand else {}})
    resp["Content-Disposition"] = f'inline; filename="receipt-{pp_id}.html"'
    return resp
