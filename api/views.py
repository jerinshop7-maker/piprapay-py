"""
REST API — port of index.php case 'api'.

Endpoints (all JSON; key in `MHS-PIPRAPAY-API-KEY` header like PHP
getAuthorizationHeader(), with Authorization/Bearer fallback):
  POST /api/checkout/redirect/   hosted checkout (scope: create_payment)
  POST /api/checkout/popup/      popup (scope: create_payment)
  POST /api/verify-payment/      fetch transaction payload (scope: verify_payment)
  POST /api/refund-payment/      refund completed transaction (scope: refund_payment)
  *    /api/<anything-else>/     400 INVALID_JSON_PAYLOAD (PHP unknown api_type)

Validation order, messages and codes mirror PHP line-for-line. Amounts must be
plain numerics (is_numeric — commas rejected, like PHP).
"""
import json
import re

from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from core import money as M
from core.models import ApiKey, Currency, Customer, Domain, Gateway, Transaction
from core.transactions import transaction_payload


def _err(code: str, message: str, status: int = 400):
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


def _auth(request):
    key = (request.META.get("HTTP_MHS_PIPRAPAY_API_KEY", "") or "").strip()
    if not key:
        key = (request.META.get("HTTP_AUTHORIZATION", "") or "").strip()
        if key.lower().startswith("bearer "):
            key = key[7:].strip()
    if not key:
        return None, "invalid"
    row = ApiKey.objects.filter(api_key=key, status="active").first()
    if not row:
        return None, "invalid"
    if M.is_expired(row.expired_date):
        return None, "expired"
    return row, ""


def _require_auth(request):
    api, problem = _auth(request)
    if api is None:
        if problem == "expired":
            return None, _err("INVALID_API_KEY", "The API key provided is incorrect or expired.")
        return None, _err("INVALID_API_KEY", "The API key provided is incorrect or invalid.")
    return api, None


def _scopes(row: ApiKey) -> list:
    try:
        scopes = json.loads(row.api_scopes or "[]")
    except (ValueError, TypeError):
        return []
    if isinstance(scopes, list):
        return scopes
    return []


def _body(request):
    try:
        data = json.loads(request.body.decode() or "")
        return data if isinstance(data, dict) else None
    except (ValueError, UnicodeDecodeError):
        return None


def _loose_domain(url: str):
    """Inline getDomainFromUrl() used by the API router (index.php:412-420):
    filter_var URL + parse_url host. No scheme default, no www-strip, no TLD
    check — looser than pp-functions getDomainValue()."""
    from urllib.parse import urlparse
    s = (url or "").strip()
    if not s:
        return False
    if "://" not in s:
        return False
    try:
        host = urlparse(s).hostname
    except Exception:
        return False
    return host or False


def _check_domain(url: str | None, kind: str):
    # Missing/empty input becomes '--'; an explicit '--' still goes through
    # validation and fails (index.php:412-420,464+).
    if url is None or url == "":
        return "--", None
    host = _loose_domain(url)
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
    if isinstance(raw, list):
        return raw, None
    if isinstance(raw, str):
        # Explicit "" is invalid JSON in PHP (json_decode("") -> null + error).
        if raw == "":
            return None, _err("INVALID_JSON", "The metadata JSON is invalid.")
        try:
            return json.loads(raw or "{}"), None
        except ValueError:
            return None, _err("INVALID_JSON", "The metadata JSON is invalid.")
    return None, _err("INVALID_METADATA", "Metadata must be an array or valid JSON string.")


def _is_numeric(value) -> bool:
    """PHP is_numeric(): optional whitespace + sign + decimals + exponent.
    No commas/thousands separators; inf/nan are NOT numeric."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    s = str(value)
    return bool(re.fullmatch(r"\s*[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?\s*", s))


def _validate_customer_fields(data):
    name = (data.get("full_name") or "").strip()
    raw_email = data.get("email_address") or ""
    mobile = (data.get("mobile_number") or "").strip()
    amount = data.get("amount", "0")
    if not name:
        return None, _err("MISSING_FIELD", "Full name is required.")
    try:
        validate_email(raw_email)
    except ValidationError:
        return None, _err("INVALID_EMAIL", "A valid email address is required.")
    email = raw_email.strip()
    if not mobile:
        return None, _err("MISSING_FIELD", "Mobile number is required.")
    if not _is_numeric(amount) or float(str(amount).strip()) <= 0:
        return None, _err("INVALID_AMOUNT", "Amount must be a positive number.")
    return {"name": name, "email": email, "mobile": mobile, "amount": str(amount).strip()}, None


def _suspend_error(brand_id: str, email: str):
    suspended = Customer.objects.filter(brand_id=brand_id, email=email, status="suspend").first()
    if not suspended:
        return None
    # PHP branches only on == "--"; empty reason still gets the suffix.
    if suspended.suspend_reason == "--" or suspended.suspend_reason is None:
        msg = "Customer is already suspended by the admin."
    else:
        msg = f"Customer is already suspended by the admin. Reason: {suspended.suspend_reason}"
    return _err("INVALID_CUSTOMER", msg)


def _ensure_customer(brand_id: str, cust: dict):
    if not Customer.objects.filter(brand_id=brand_id, email=cust["email"]).exists():
        Customer.objects.create(ref=M.generate_item_id(), brand_id=brand_id, name=cust["name"],
                                email=cust["email"], mobile=cust["mobile"], inserted_via="checkout")


def _pp_url(request, pp_id: str) -> str:
    return request.build_absolute_uri("/").rstrip("/") + f"/payment/{pp_id}"


def _empty_metadata_json(metadata) -> str:
    if isinstance(metadata, dict) and not metadata:
        return "[]"
    if isinstance(metadata, list) and not metadata:
        return "[]"
    return json.dumps(metadata, ensure_ascii=False)


@csrf_exempt
def checkout_redirect(request):
    if request.method != "POST":
        return _err("INVALID_JSON_PAYLOAD", "The JSON payload is invalid or malformed.")
    api, err = _require_auth(request)
    if err:
        return err
    data = _body(request)
    if not data:
        return _err("INVALID_JSON_PAYLOAD", "The JSON payload is invalid or malformed.")
    if "create_payment" not in _scopes(api):
        return _err("INSUFFICIENT_SCOPE", "The API key does not have the required permission: Create Payment")

    return_url, err = _check_domain(data.get("return_url"), "Return")
    if err:
        return err
    webhook_url, err = _check_domain(data.get("webhook_url"), "Webhook")
    if err:
        return err
    metadata, err = _parse_metadata(data.get("metadata", "{}") if "metadata" in data else "{}")
    if err:
        return err
    cust, err = _validate_customer_fields(data)
    if err:
        return err
    currency = data.get("currency", "BDT")

    if not Currency.objects.filter(brand_id=api.brand_id, code=currency).exists():
        return _err("INVALID_CURRENCY", "Currency not supported.")
    err = _suspend_error(api.brand_id, cust["email"])
    if err:
        return err

    pp_id = M.generate_item_id(27, 27)
    Transaction.objects.create(
        brand_id=api.brand_id, source="api", ref=pp_id,
        customer_info=json.dumps({"name": cust["name"], "email": cust["email"], "mobile": cust["mobile"]},
                                 ensure_ascii=False),
        amount=M.money_sanitize(cust["amount"]), currency=currency,
        metadata=_empty_metadata_json(metadata), return_url=return_url, webhook_url=webhook_url)
    _ensure_customer(api.brand_id, cust)
    return JsonResponse({"pp_id": pp_id, "pp_url": _pp_url(request, pp_id)})


@csrf_exempt
def checkout_popup(request):
    if request.method != "POST":
        return _err("INVALID_JSON_PAYLOAD", "The JSON payload is invalid or malformed.")
    api, err = _require_auth(request)
    if err:
        return err
    data = _body(request)
    if not data:
        return _err("INVALID_JSON_PAYLOAD", "The JSON payload is invalid or malformed.")
    if "create_payment" not in _scopes(api):
        return _err("INSUFFICIENT_SCOPE", "The API key does not have the required permission: Create Payment")
    webhook_url = data.get("webhook_url")
    webhook_url = "--" if webhook_url is None or webhook_url == "" else webhook_url
    metadata, err = _parse_metadata(data.get("metadata", "{}") if "metadata" in data else "{}")
    if err:
        return err
    cust, err = _validate_customer_fields(data)
    if err:
        return err
    currency = data.get("currency", "BDT")
    if not Currency.objects.filter(brand_id=api.brand_id, code=currency).exists():
        return _err("INVALID_CURRENCY", "Currency not supported.")
    err = _suspend_error(api.brand_id, cust["email"])
    if err:
        return err
    pp_id = M.generate_item_id(27, 27)
    Transaction.objects.create(
        brand_id=api.brand_id, source="api", ref=pp_id,
        customer_info=json.dumps({"name": cust["name"], "email": cust["email"], "mobile": cust["mobile"]},
                                 ensure_ascii=False),
        amount=M.money_sanitize(cust["amount"]), currency=currency,
        metadata=_empty_metadata_json(metadata), return_url="--", webhook_url=webhook_url)
    _ensure_customer(api.brand_id, cust)
    return JsonResponse({"pp_id": pp_id, "pp_url": _pp_url(request, pp_id)})


@csrf_exempt
def verify_payment(request):
    if request.method != "POST":
        return _err("INVALID_JSON_PAYLOAD", "The JSON payload is invalid or malformed.")
    api, err = _require_auth(request)
    if err:
        return err
    data = _body(request)
    if not data:
        return _err("INVALID_JSON_PAYLOAD", "The JSON payload is invalid or malformed.")
    if "verify_payment" not in _scopes(api):
        return _err("INSUFFICIENT_SCOPE", "The API key does not have the required permission: Verify Payment")
    pp_id = data.get("pp_id") or ""
    if not pp_id:
        return _err("INVALID_PP_ID", "A valid bp id is required.")
    txn = Transaction.objects.filter(ref=pp_id).first()
    if not txn:
        return _err("INVALID_PP_ID", "A valid bp id is required.")
    return JsonResponse(transaction_payload(txn, round_total=True))


@csrf_exempt
def refund_payment(request):
    if request.method != "POST":
        return _err("INVALID_JSON_PAYLOAD", "The JSON payload is invalid or malformed.")
    api, err = _require_auth(request)
    if err:
        return err
    data = _body(request)
    if not data:
        return _err("INVALID_JSON_PAYLOAD", "The JSON payload is invalid or malformed.")
    if "refund_payment" not in _scopes(api):
        return _err("INSUFFICIENT_SCOPE", "The API key does not have the required permission: Refund Payment")
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
        changed = False
        for label, key in (("Refund TrxID", "refundTrxId"), ("Refund Amount", "refundAmount"),
                           ("Refund Time", "completedTime")):
            val = (result.get("data") or {}).get(key, "")
            if val:
                info.append({"label": label, "value": val})
                changed = True
        # source_info rewritten only when a refund field was appended.
        if changed:
            txn.source_info = json.dumps(info, ensure_ascii=False)

    txn.status = "refunded"
    txn.save()
    payload = transaction_payload(txn, round_total=True)
    payload["status"] = "refunded"
    return JsonResponse(payload)


@csrf_exempt
def unknown_api(request, path: str = ""):
    """Unknown api_type / checkout_type -> 400 INVALID_JSON_PAYLOAD (PHP parity)."""
    return _err("INVALID_JSON_PAYLOAD", "The JSON payload is invalid or malformed.")
