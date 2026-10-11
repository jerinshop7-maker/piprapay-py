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

from core import money as M
from core.models import Brand, Gateway
from core.transactions import gateway_options, set_transaction_status
from gateways.registry import get_gateway
from themes.twenty_six import get_theme


def _brand_info(brand: Brand | None) -> dict:
    # PHP passes the RAW name (no '--' fallback) in the actually-dispatched
    # $response['brand']; logo/favicon fall back only on '--'.
    if not brand:
        return {"id": ""}
    return {
        "id": brand.brand_id,
        "name": brand.name,
        "identifyName": brand.identify_name,
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
        "locale": {"timezone": brand.timezone, "language": language,
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
    info = theme.build_lang(language)
    data = {
        "gateway": {"gateway_id": gw.gateway_id, "slug": gw.slug, "name": gw.name,
                    "display": gw.display, "logo": gw.logo, "currency": gw.currency,
                    "min_allow": M.money_round(str(gw.min_allow)),
                    "max_allow": M.money_round(str(gw.max_allow)),
                    "fixed_discount": M.money_round(str(gw.fixed_discount)),
                    "percentage_discount": M.money_round(str(gw.percentage_discount)),
                    "fixed_charge": M.money_round(str(gw.fixed_charge)),
                    "percentage_charge": M.money_round(str(gw.percentage_charge)),
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
    if result.get("canceled") and result.get("pp_ref"):
        # Failed-verification cancel with a derived ref (shurjoPay):
        # PHP writes canceled. (User-abort callbacks stay initiated.)
        set_transaction_status(result["pp_ref"], "canceled")
        return JsonResponse({"status": "ignored"})
    code = result.get("http_code", 200)
    if code != 200:
        return JsonResponse({"status": "ignored"}, status=code)
    return JsonResponse({"status": "ignored"})
