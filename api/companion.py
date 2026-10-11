"""
Android companion-device API — port of $_POST['action-companion'] handlers
(pp-adapter.php:9147-9671): login (OTP pairing), account-information,
sms-transmit-bulk (parse + balance-gated store), sms-transmit-sender,
delete-sms-data. Token = device.otp of a `used` device row.

Note: $pp_demo_mode gates are not ported (no demo-mode concept in Django).
"""
import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from core import money as M
from core.env import get_env
from core.models import Admin, BrowserLog, Device, SmsData
from core.sms_engine import (mfs_message_verified, reconcile_by_longest_chain,
                             sender_whitelist)


def _device_by_token(token: str):
    if not token:
        return None
    return Device.objects.filter(otp=token, status="used").first()


def _fail(title: str, message: str):
    return JsonResponse({"status": "false", "title": title, "message": message})


@csrf_exempt
def companion(request, action: str):
    if request.method != "POST":
        return _fail("Oops! Something went wrong", "Your request could not be processed. Please try again.")
    handler = {"login": _login, "account-information": _account_information,
               "sms-transmit-bulk": _sms_transmit_bulk, "sms-transmit-sender": _sms_transmit_sender,
               "delete-sms-data": _delete_sms_data}.get(action)
    if not handler:
        return _fail("Oops! Something went wrong", "Your request could not be processed. Please try again.")
    return handler(request)


def _login(request):
    otp = (request.POST.get("onetimepassword") or "").strip()
    if not otp:
        return _fail("Incomplete Information", "Please fill in all required fields before proceeding.")
    row = Device.objects.filter(otp=otp).first()
    if not row:
        return _fail("Invalid Credentials", "Please enter the correct credentials or scan the QR code again.")
    # PHP overwrites device details verbatim, even with empty POST values.
    row.otp = M.generate_item_id()
    row.name = request.POST.get("name", "")
    row.model = request.POST.get("model", "")
    row.android_level = request.POST.get("android_level", "")
    row.app_version = request.POST.get("app_version", "")
    row.status = "used"
    row.save()
    return JsonResponse({"status": "true", "token": row.otp})


def _account_information(request):
    device = _device_by_token((request.POST.get("token") or "").strip())
    if not device:
        return _fail("Authentication Failed", "Please try again or scan the QR code again.")
    log = BrowserLog.objects.filter(cookie=device.d_id).first()
    admin = Admin.objects.filter(a_id=log.a_id).first() if log else None
    if not admin:
        return _fail("Authentication Failed", "Please try again or scan the QR code again.")
    resp = {"status": "true", "fullname": admin.full_name or "", "email": admin.email or "",
            "stored_count": 0, "used_count": 0, "error_count": 0,
            "stored": [], "used": [], "error": []}
    tz = get_env("geneal-application-settings-default_timezone")
    if tz in ("--", "", None):
        tz = "Asia/Dhaka"
    for row in SmsData.objects.filter(source="app", device_id=device.device_id
                                      ).exclude(status="awaiting-review").order_by("-id"):
        item = {"id": row.id, "sender": row.sender, "message": row.message or "",
                "reason": row.reason or "", "simslot": row.simslot or "",
                "timestamp": M.convert_utc_to_user_tz(row.created_date, tz, "%b %d, %Y %I:%M %p"),
                "status": "stored" if row.status == "approved" else row.status}
        if row.status in ("approved", "awaiting-review"):
            resp["stored"].append(item)
            resp["stored_count"] += 1
        elif row.status == "used":
            resp["used"].append(item)
            resp["used_count"] += 1
        elif row.status == "error":
            resp["error"].append(item)
            resp["error_count"] += 1
    return JsonResponse(resp)


def _sms_transmit_bulk(request):
    from core.models import BalanceVerification
    device = _device_by_token((request.POST.get("token") or "").strip())
    if not device:
        return _fail("Authentication Failed", "Please try again or scan the QR code again.")
    try:
        sms_list = json.loads(request.POST.get("sms_list", "") or "[]")
    except ValueError:
        sms_list = []
    device_id = device.device_id
    failed = False
    for sms in sms_list:
        sender = str(sms.get("sender", "")).lower().strip()
        message = str(sms.get("message", "")).strip()
        simslot = str(sms.get("simSlot", "")).strip()
        info = sender_whitelist(sender=sender)
        if info:
            sender_key, currency, balance_verify = info["provider_key"], info["currency"], info["balance_verify"]
        else:
            sender_key, currency, balance_verify = "--", "--", "--"
        result = mfs_message_verified(sender_key, message)
        if result is False:
            SmsData.objects.create(source="app", device_id=device_id, sender=sender, simslot=simslot,
                                   status="error", message=message,
                                   reason="Invalid or unknown message. Code 101")
            device.last_sync = M.get_current_datetime()
            device.save(update_fields=["last_sync", "updated_date"])
            return _fail("Invalid or unknown MFS message",
                         "Please fill in all required fields before proceeding.")
        sms_type = result.get("type", "") or ""
        amount = result.get("amount", "") or "0"
        balance = result.get("balance", "") or "0"
        number = result.get("sender", "") or ""
        trx_id = result.get("trxid", "") or ""
        if not sms_type or not amount or not number or not trx_id:
            SmsData.objects.create(source="app", device_id=device_id, sender=sender, simslot=simslot,
                                   status="error", message=message,
                                   reason="Invalid or unknown message. Code 102")
            device.last_sync = M.get_current_datetime()
            device.save(update_fields=["last_sync", "updated_date"])
            return _fail("Invalid or unknown MFS message",
                         "Please fill in all required fields before proceeding.")
        if SmsData.objects.filter(sender_key=sender_key, trx_id=trx_id).exists():
            # Duplicate: PHP inserts an error row (Code 103) and keeps going.
            SmsData.objects.create(source="app", device_id=device_id, sender=sender, simslot=simslot,
                                   status="error", message=message,
                                   reason="Duplicate Transaction. Code 103")
            device.last_sync = M.get_current_datetime()
            device.save(update_fields=["last_sync", "updated_date"])
            failed = True
            continue
        if balance_verify == "false":
            SmsData.objects.create(
                source="app", device_id=device_id, sender=sender, sender_key=sender_key, simslot=simslot,
                number=number, amount=M.money_sanitize(amount), currency=currency, trx_id=trx_id,
                balance=M.money_sanitize(balance), type=sms_type, status="approved",
                message=message, reason="--")
        else:
            rule = BalanceVerification.objects.filter(device_id=device_id, sender_key=sender_key,
                                                      type=sms_type).first()
            if rule and rule.status == "active":
                bsimslot = "Sim1" if str(simslot) == "1" else "Sim2"
                expected = M.money_add(str(rule.current_balance), amount)
                status, reason = "approved", "--"
                # PHP `==` is numeric juggling, NOT string equality.
                if not M.num_eq(expected, M.money_sanitize(balance)):
                    reasons = [f"SMS balance and expected balance do not match. Recorded SMS balance: "
                               f"{M.money_round(balance)}; Expected balance: {M.money_round(expected)}"]
                    status = "awaiting-review"
                    if rule.simslot != "Any" and rule.simslot != bsimslot:
                        reasons.append(f"SIM slot and expected slot do not match. Recorded: {bsimslot}; "
                                       f"Expected: {rule.simslot}")
                    reason = " | ".join(reasons)
                elif rule.simslot != "Any" and rule.simslot != bsimslot:
                    status = "awaiting-review"
                    reason = (f"SIM slot and expected slot do not match. Recorded: {bsimslot}; "
                              f"Expected: {rule.simslot}")
                else:
                    rule.current_balance = M.money_sanitize(expected)
                    rule.save(update_fields=["current_balance", "updated_date"])
                SmsData.objects.create(
                    source="app", device_id=device_id, sender=sender, sender_key=sender_key, simslot=simslot,
                    number=number, amount=M.money_sanitize(amount), currency=currency, trx_id=trx_id,
                    balance=M.money_sanitize(balance), type=sms_type, status=status,
                    message=message, reason=reason)
                if status == "awaiting-review" and not M.num_eq(expected, M.money_sanitize(balance)):
                    reconcile_by_longest_chain(device_id, sender_key, sms_type)
            else:
                SmsData.objects.create(
                    source="app", device_id=device_id, sender=sender, sender_key=sender_key, simslot=simslot,
                    number=number, amount=M.money_sanitize(amount), currency=currency, trx_id=trx_id,
                    balance=M.money_sanitize(balance), type=sms_type, status="approved",
                    message=message, reason="--")
        device.last_sync = M.get_current_datetime()
        device.save(update_fields=["last_sync", "updated_date"])
    if failed:
        return _fail("Invalid or unknown MFS message",
                     "Please fill in all required fields before proceeding.")
    return JsonResponse({"status": "true", "title": "SMS Data Created",
                         "message": "The sms data has been created successfully."})


def _sms_transmit_sender(request):
    device = _device_by_token((request.POST.get("token") or "").strip())
    if not device:
        return _fail("Authentication Failed", "Please try again or scan the QR code again.")
    return JsonResponse({"status": "true", "senders": sender_whitelist(mode="senders")})


def _delete_sms_data(request):
    device = _device_by_token((request.POST.get("token") or "").strip())
    if not device:
        return _fail("Authentication Failed", "Please try again or scan the QR code again.")
    if request.POST.get("stored") == "yes":
        SmsData.objects.filter(device_id=device.device_id, status="approved").delete()
        SmsData.objects.filter(device_id=device.device_id, status="awaiting-review").delete()
    if request.POST.get("used") == "yes":
        SmsData.objects.filter(device_id=device.device_id, status="used").delete()
    if request.POST.get("error") == "yes":
        SmsData.objects.filter(device_id=device.device_id, status="error").delete()
    return JsonResponse({"status": "true", "title": "Deletion Successful",
                         "message": "The selected data has been deleted successfully."})
