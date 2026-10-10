"""
IPN endpoint — mirrors index.php case 'ipn' (ipn/<gateway_id>).
Looks up gateway row + brand, builds options/lang/brand context, dispatches
to plugin ipn()/callback(), completes transaction on verification.
"""
import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from core.models import Brand, Gateway, GatewayParameter
from core.transactions import set_transaction_status
from gateways.registry import get_gateway


def _options(gateway_id: str) -> dict:
    return {p.option_name: p.value for p in GatewayParameter.objects.filter(gateway_id=gateway_id)}


@csrf_exempt
def ipn_entry(request, gateway_id: str):
    gw = Gateway.objects.filter(gateway_id=gateway_id).first()
    if not gw:
        return JsonResponse({"error": {"code": "INVALID_GATEWAY",
                                       "message": "The Gateway provided is incorrect or invalid."}}, status=400)
    plugin = get_gateway(gw.slug)
    if not plugin:
        return JsonResponse({"error": {"code": "INVALID_GATEWAY", "message": "Gateway plugin missing."}}, status=400)
    brand = Brand.objects.filter(brand_id=gw.brand_id).first()
    data = {
        "gateway": {"gateway_id": gw.gateway_id, "slug": gw.slug, "options": _options(gateway_id)},
        "brand": {"id": brand.brand_id if brand else gw.brand_id},
        "options": _options(gateway_id),
        "query": dict(request.GET),
        "post": dict(request.POST),
        "headers": {k[5:].replace("_", "-").lower(): v for k, v in request.META.items() if k.startswith("HTTP_")},
        "raw_body": request.body,
    }
    result = plugin.ipn(data)
    if result.get("verified"):
        pp_id = request.GET.get("pp_id") or request.POST.get("pp_id") or ""
        if pp_id:
            ok = set_transaction_status(pp_id, "completed", gateway_id,
                                        result.get("trx_id", ""), result.get("source_info", []))
            return JsonResponse({"status": "ok" if ok else "not-matched"})
    return JsonResponse({"status": "ignored"})
