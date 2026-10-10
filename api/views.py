"""
REST API — port of index.php case 'api'.

Endpoints (all JSON, Bearer/API-key header auth like PHP getAuthorizationHeader):
  POST /api/checkout/redirect/   create hosted-checkout transaction (scope: create_payment)
  POST /api/checkout/popup/      create popup transaction (scope: create_payment)
  POST /api/verify-payment/      fetch transaction payload (scope: verify_payment)
  POST /api/refund-payment/      refund completed transaction (scope: refund_payment)

Error codes mirror PHP exactly: INVALID_API_KEY, INVALID_JSON_PAYLOAD,
INSUFFICIENT_SCOPE, INVALID_URL, INVALID_JSON, INVALID_METADATA,
MISSING_FIELD, INVALID_EMAIL, INVALID_AMOUNT, INVALID_CURRENCY,
INVALID_CUSTOMER, INVALID_PP_ID, INVALID_STATUS, REFUND_FAILED.
"""
import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from core import money as M
from core.models import ApiKey, Brand, Currency, Customer, Domain, Gateway, Transaction
from core.transactions import transaction_payload


def _err(code: str, message: str, status: int = 400):
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


def _auth(request):
    key = (request.META.get("HTTP_AUTHORIZATION", "") or "").strip()
    if key.lower().startswith("bearer "):
        key = key[7:].strip()
    if not key:
        return None
    row = ApiKey.objects.filter(api_key=key, status="active").first()
    if not row or M.is_expired(row.expired_date):
        return None
    return row


def _scopes(row: ApiKey) -> list:
    try:
        scopes = json.loads(row.api_scopes or "[]")
        return scopes if isinstance(scopes, list) else []
    except ValueError:
        return []


def _body(request):
    try:
        data = json.loads(request.body.decode() or "")
        return data if isinstance(data, dict) else None
    except (ValueError, UnicodeDecodeError):
        return None


def _check_domain(url: str, kind: str):
    if url in ("", "--"):
        return "--", None
    host = M.get_domain_value(url)
    if not host:
        return None, _err("INVALID_URL", f"{kind} URL is invalid.")
    row = Domain.objects.filter(domain=host).first()
    if not row:
        return None, _err("INVALID_URL",
                           f'The provided {kind} URL ("{host}") is not whitelisted. '
                           f'Please add this domain in the "Domains" section to continue.')
    if row.status != "active":
        return None, _err("INVALID_URL",
                           f'The {kind} URL ("{host}") is whitelisted but not active. '
                           f'Please activate this domain in the "Domains" section to proceed.')
    return url, None


def _parse_metadata(raw):
    if isinstance(raw, dict):
        return raw, None
    if isinstance(raw, str):
        try:
            return json.loads(raw or "{}"), None
        except ValueError:
            return None, _err("INVALID_JSON", "The metadata JSON is invalid.")
    return None, _err("INVALID_METADATA", "Metadata must be an array or valid JSON string.")


def _validate_customer_fields(data):
    name = (data.get("full_name") or "").strip()
    email = (data.get("email_address") or "").strip()
    mobile = (data.get("mobile_number") or "").strip()
    amount = data.get("amount", "0")
    if not name:
        return None, _err("MISSING_FIELD", "Full name is required.")
    if not email or "@" not in email:
        return None, _err("INVALID_EMAIL", "A valid email address is required.")
    if not mobile:
        return None, _err("MISSING_FIELD", "Mobile number is required.")
    try:
        if float(str(amount).replace(",", "")) <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return None, _err("INVALID_AMOUNT", "Amount must be a positive number.")
    return {"name": name, "email": email, "mobile": mobile, "amount": amount}, None


@csrf_exempt
def checkout_redirect(request):
    if request.method != "POST":
        return _err("INVALID_JSON_PAYLOAD", "The JSON payload is invalid or malformed.")
    api = _auth(request)
    if not api:
        return _err("INVALID_API_KEY", "The API key provided is incorrect or invalid.")
    if "create_payment" not in _scopes(api):
        return _err("INSUFFICIENT_SCOPE", "The API key does not have the required permission: Create Payment")
    data = _body(request)
    if not data:
        return _err("INVALID_JSON_PAYLOAD", "The JSON payload is invalid or malformed.")

    return_url, err = _check_domain(data.get("return_url") or "--", "Return")
    if err:
        return err
    webhook_url, err = _check_domain(data.get("webhook_url") or "--", "Webhook")
    if err:
        return err
    metadata, err = _parse_metadata(data.get("metadata", "{}"))
    if err:
        return err
    cust, err = _validate_customer_fields(data)
    if err:
        return err
    currency = data.get("currency", "BDT")

    if not Currency.objects.filter(brand_id=api.brand_id, code=currency).exists():
        return _err("INVALID_CURRENCY", "Currency not supported.")
    suspended = Customer.objects.filter(brand_id=api.brand_id, email=cust["email"], status="suspend").first()
    if suspended:
        reason = suspended.suspend_reason
        msg = "Customer is already suspended by the admin." if (not reason or reason == "--") \
            else f"Customer is already suspended by the admin. Reason: {reason}"
        return _err("INVALID_CUSTOMER", msg)

    pp_id = M.generate_item_id(27, 27)
    Transaction.objects.create(
        brand_id=api.brand_id, source="api", ref=pp_id,
        customer_info=json.dumps({"name": cust["name"], "email": cust["email"], "mobile": cust["mobile"]}),
        amount=M.money_sanitize(cust["amount"]), currency=currency,
        metadata=json.dumps(metadata), return_url=return_url, webhook_url=webhook_url)
    if not Customer.objects.filter(brand_id=api.brand_id, email=cust["email"]).exists():
        Customer.objects.create(ref=M.generate_item_id(), brand_id=api.brand_id, name=cust["name"],
                                email=cust["email"], mobile=cust["mobile"], inserted_via="checkout")
    base = request.build_absolute_uri("/") .rstrip("/")
    return JsonResponse({"pp_id": pp_id, "pp_url": f"{base}/payment/{pp_id}"})


@csrf_exempt
def checkout_popup(request):
    if request.method != "POST":
        return _err("INVALID_JSON_PAYLOAD", "The JSON payload is invalid or malformed.")
    api = _auth(request)
    if not api:
        return _err("INVALID_API_KEY", "The API key provided is incorrect or invalid.")
    if "create_payment" not in _scopes(api):
        return _err("INSUFFICIENT_SCOPE", "The API key does not have the required permission: Create Payment")
    data = _body(request)
    if not data:
        return _err("INVALID_JSON_PAYLOAD", "The JSON payload is invalid or malformed.")
    webhook_url = data.get("webhook_url") or "--"
    metadata, err = _parse_metadata(data.get("metadata", "{}"))
    if err:
        return err
    cust, err = _validate_customer_fields(data)
    if err:
        return err
    currency = data.get("currency", "BDT")
    if not Currency.objects.filter(brand_id=api.brand_id, code=currency).exists():
        return _err("INVALID_CURRENCY", "Currency not supported.")
    pp_id = M.generate_item_id(27, 27)
    Transaction.objects.create(
        brand_id=api.brand_id, source="api", ref=pp_id,
        customer_info=json.dumps({"name": cust["name"], "email": cust["email"], "mobile": cust["mobile"]}),
        amount=M.money_sanitize(cust["amount"]), currency=currency,
        metadata=json.dumps(metadata), return_url="--", webhook_url=webhook_url)
    base = request.build_absolute_uri("/").rstrip("/")
    return JsonResponse({"pp_id": pp_id, "pp_url": f"{base}/payment/{pp_id}"})


@csrf_exempt
def verify_payment(request):
    if request.method != "POST":
        return _err("INVALID_JSON_PAYLOAD", "The JSON payload is invalid or malformed.")
    api = _auth(request)
    if not api:
        return _err("INVALID_API_KEY", "The API key provided is incorrect or invalid.")
    if "verify_payment" not in _scopes(api):
        return _err("INSUFFICIENT_SCOPE", "The API key does not have the required permission: Verify Payment")
    data = _body(request)
    if not data:
        return _err("INVALID_JSON_PAYLOAD", "The JSON payload is invalid or malformed.")
    pp_id = data.get("pp_id") or ""
    if not pp_id:
        return _err("INVALID_PP_ID", "A valid bp id is required.")
    txn = Transaction.objects.filter(ref=pp_id).first()
    if not txn:
        return _err("INVALID_PP_ID", "A valid bp id is required.")
    return JsonResponse(transaction_payload(txn))


@csrf_exempt
def refund_payment(request):
    if request.method != "POST":
        return _err("INVALID_JSON_PAYLOAD", "The JSON payload is invalid or malformed.")
    api = _auth(request)
    if not api:
        return _err("INVALID_API_KEY", "The API key provided is incorrect or invalid.")
    if "refund_payment" not in _scopes(api):
        return _err("INSUFFICIENT_SCOPE", "The API key does not have the required permission: Refund Payment")
    data = _body(request)
    if not data:
        return _err("INVALID_JSON_PAYLOAD", "The JSON payload is invalid or malformed.")
    pp_id = data.get("pp_id") or ""
    if not pp_id:
        return _err("INVALID_PP_ID", "A valid bp id is required.")
    txn = Transaction.objects.filter(ref=pp_id).first()
    if not txn:
        return _err("INVALID_PP_ID", "A valid bp id is required.")
    if txn.status != "completed":
        return _err("INVALID_STATUS", "Only completed transactions can be refunded.")

    gw = Gateway.objects.filter(gateway_id=txn.gateway_id, brand_id=txn.brand_id).first()
    if gw and gw.slug == "bkash-api-tokenized":
        from gateways.bkash_refund import bkash_tokenized_refund
        result = bkash_tokenized_refund(txn, {"amount": M.money_round(str(txn.local_net_amount)),
                                              "sku": txn.ref, "reason": "API refund"})
        if not result.get("status"):
            return _err("REFUND_FAILED", result.get("message", "Refund failed."))
        try:
            info = json.loads(txn.source_info or "[]")
            if not isinstance(info, list):
                info = []
        except ValueError:
            info = []
        for label, key in (("Refund TrxID", "refundTrxId"), ("Refund Amount", "refundAmount"), ("Refund Time", "completedTime")):
            val = (result.get("data") or {}).get(key, "")
            if val:
                info.append({"label": label, "value": val})
        txn.source_info = json.dumps(info)

    txn.status = "refunded"
    txn.save()
    payload = transaction_payload(txn)
    payload["status"] = "refunded"
    return JsonResponse(payload)
