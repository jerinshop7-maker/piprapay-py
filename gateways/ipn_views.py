"""
IPN endpoint — mirrors index.php case 'ipn' (ipn/<gateway_id>).

- Unknown gateway_id / orphaned brand / missing plugin class
  -> 400 INVALID_GATEWAY (same code+message as PHP).
- Builds the full gateway+brand+lang context PHP passes to ipn(), then
  dispatches to plugin ipn(). Plugins return {verified, trx_id, source_info}
  plus optional pp_ref (ref resolved by the plugin, e.g. Binance bpid,
  ShurjoPay split, NOWPayments/OxaPay order_id), redirect, or json payload.
"""
import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from core.models import Brand, Gateway
from core.transactions import gateway_options, set_transaction_status
from gateways.registry import get_gateway
from themes.twenty_six import get_theme


def _brand_info(brand: Brand | None) -> dict:
    if not brand:
        return {"id": ""}
    return {
        "id": brand.brand_id,
        "name": brand.name if brand.name not in ("--", "", None) else brand.identify_name,
        "identifyName": brand.identify_name,
        "logo": None if brand.logo in ("--", "", None) else brand.logo,
        "favicon": None if brand.favicon in ("--", "", None) else brand.favicon,
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


def _invalid():
    return JsonResponse({"error": {"code": "INVALID_GATEWAY",
                                   "message": "The Gateway provided is incorrect or invalid."}}, status=400)


@csrf_exempt
def ipn_entry(request, gateway_id: str):
    gw = Gateway.objects.filter(gateway_id=gateway_id).first()
    if not gw:
        return _invalid()
    brand = Brand.objects.filter(brand_id=gw.brand_id).first()
    if not brand:
        return _invalid()
    plugin = get_gateway(gw.slug)
    if not plugin:
        return _invalid()

    theme = get_theme(brand.theme)
    language = theme.resolve_language(brand.language or "en")
    options = gateway_options(gw.gateway_id, gw.brand_id)
    data = {
        "gateway": {"gateway_id": gw.gateway_id, "slug": gw.slug, "name": gw.name,
                    "display": gw.display, "logo": gw.logo, "currency": gw.currency,
                    "min_allow": str(gw.min_allow), "max_allow": str(gw.max_allow),
                    "fixed_discount": str(gw.fixed_discount),
                    "percentage_discount": str(gw.percentage_discount),
                    "fixed_charge": str(gw.fixed_charge),
                    "percentage_charge": str(gw.percentage_charge),
                    "primary_color": gw.primary_color, "text_color": gw.text_color,
                    "btn_color": gw.btn_color, "btn_text_color": gw.btn_text_color,
                    "options": options},
        "brand": _brand_info(brand),
        "options": options,
        "lang": theme.build_lang(language),
        "query": request.GET.dict(),
        "post": request.POST.dict(),
        "headers": {k[5:].replace("_", "-").lower(): v for k, v in request.META.items()
                    if k.startswith("HTTP_")},
        "raw_body": request.body,
        "callback_url": request.build_absolute_uri(),
    }
    result = plugin.ipn(data) or {}
    if result.get("json") is not None:
        return JsonResponse(result["json"])
    if result.get("verified"):
        from core.models import Transaction
        pp_ref = result.get("pp_ref") or (data["query"].get("pp_id") or data["post"].get("pp_id") or "")
        txn = Transaction.objects.filter(ref=pp_ref).first() if pp_ref else None
        ok = False
        if txn:
            data["transaction"] = {"ref": txn.ref}
            ok = set_transaction_status(pp_ref, "completed", gateway_id,
                                        result.get("trx_id", ""), result.get("source_info", []))
        if result.get("redirect"):
            from django.shortcuts import redirect
            target = result["redirect"]
            if target == "checkout" and pp_ref:
                # ShurjoPay-style: complete, then back to checkout (PHP redirect).
                return redirect(f"/payment/{pp_ref}/")
            return redirect(target)
        return JsonResponse({"status": "ok" if ok else "not-matched"})
    code = result.get("http_code", 200)
    if code != 200:
        return JsonResponse({"status": "ignored"}, status=code)
    return JsonResponse({"status": "ignored"})
