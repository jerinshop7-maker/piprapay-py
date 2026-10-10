"""
Cron runner — port of index.php case $path_cron.

Steps (same order as PHP):
1. lock file guard (10 min) + set last-cron-invocation
2. auto system-update check (10h, stable|beta channel via manifest)
3. verify pending transactions against approved sms_data (tolerance-aware)
4. auto currency exchange refresh (5h per brand, fawazahmed0 API)
5. balance verification reconcile per active rule
6. webhook retry queue (15 oldest pending, attempts limit, completed|canceled)
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from django.conf import settings
from django.utils import timezone

from core import hooks, money as M
from core.env import get_env, set_env
from core.models import BalanceVerification, Brand, Currency, Gateway, SmsData, Transaction, WebhookLog
from core.sms_engine import reconcile_by_longest_chain
from core.webhooks import send_ipn_multi

LOCK_FILE = Path(settings.BASE_DIR) / "media" / "storage" / "cron.lock"
MAX_LOCK_AGE = 60 * 10


def acquire_lock() -> bool:
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    if LOCK_FILE.exists() and (time.time() - LOCK_FILE.stat().st_mtime) < MAX_LOCK_AGE:
        return False
    LOCK_FILE.write_text(str(time.time()))
    return True


def release_lock() -> None:
    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


def check_updates() -> None:
    auto = get_env("system-settings-automatic_update")
    if auto != "yes":
        return
    last = get_env("last-auto-update-check")
    now = M.get_current_datetime()
    try:
        from datetime import datetime
        last_dt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
        now_dt = datetime.strptime(now, "%Y-%m-%d %H:%M:%S")
        if (now_dt - last_dt).total_seconds() < 10 * 3600:
            return
    except (ValueError, TypeError):
        pass
    set_env("last-auto-update-check", now)
    try:
        manifest = requests.get(settings.PIPRAPAY_UPDATE_MANIFEST_URL, timeout=15).json()
        channel = get_env("system-settings-update_channel") or "stable"
        if channel not in ("stable", "beta"):
            channel = "stable"
        ch = (manifest.get("channels") or {}).get(channel) or {}
        current = settings.PIPRAPAY_VERSION["version_code"]
        latest = ch.get("latest_version_code")
        if latest and str(latest) > str(current):
            hooks.do_action("system.update.available", {
                "current_version_name": settings.PIPRAPAY_VERSION["version_name"],
                "current_version_code": current,
                "latest_version_name": ch.get("latest_version_name"),
                "latest_version_code": latest})
            set_env("last-update-version-name", ch.get("latest_version_name", ""))
            set_env("last-update-version", str(latest))
        else:
            set_env("last-update-version-name", settings.PIPRAPAY_VERSION["version_name"])
            set_env("last-update-version", str(current))
    except Exception:
        pass


def verify_pending() -> list:
    done = []
    pendings = Transaction.objects.filter(status="pending").exclude(sender_key__in=["--", ""]).order_by("-id")
    for row in pendings:
        sms = SmsData.objects.filter(sender_key=row.sender_key, type=row.sender_type,
                                     trx_id=row.trx_id, status="approved").first()
        if not sms:
            continue
        brand = Brand.objects.filter(brand_id=row.brand_id).first()
        tolerance = brand.payment_tolerance if brand else "0"
        if not M.verify_payment_tolerance(str(row.local_net_amount or row.amount), str(sms.amount), tolerance):
            continue
        sms.status = "used"
        sms.save(update_fields=["status", "updated_date"])
        row.status = "completed"
        row.sender = sms.number
        row.save()
        try:
            customer = json.loads(row.customer_info or "{}")
            metadata = json.loads(row.metadata or "{}")
        except ValueError:
            customer, metadata = {}, {}
        gw = Gateway.objects.filter(gateway_id=row.gateway_id).first()
        net = M.money_sub(M.money_add(str(row.amount), str(row.processing_fee)), str(row.discount_amount))
        item = {"pp_id": row.ref, "full_name": customer.get("name", "N/A"),
                "email_address": customer.get("email", "N/A"), "mobile_number": customer.get("mobile", "N/A"),
                "gateway": gw.display if gw else "", "amount": M.money_round(str(row.amount)),
                "fee": M.money_round(str(row.processing_fee)), "discount_amount": M.money_round(str(row.discount_amount)),
                "total": M.money_round(net), "local_net_amount": M.money_round(str(row.local_net_amount)),
                "currency": row.currency, "local_currency": row.local_currency, "metadata": metadata,
                "sender": sms.number, "transaction_id": row.trx_id, "status": row.status,
                "date": M.convert_utc_to_user_tz(row.created_date, brand.timezone if brand and brand.timezone not in ("--", "") else "Asia/Dhaka", "%b %d, %Y %I:%M %p")}
        done.append(item)
        if row.webhook_url and row.webhook_url != "--":
            WebhookLog.objects.create(ref=str(row.id)[:15], brand_id=row.brand_id,
                                      payload=json.dumps(item), url=row.webhook_url)
    if done:
        hooks.do_action("transactions.updated", done)
    return done


def refresh_currency() -> None:
    from datetime import datetime

    now = M.get_current_datetime()
    jobs = []
    for brand in Brand.objects.filter(autoExchange="enabled"):
        last = get_env("last-auto-exchange", brand.brand_id)
        try:
            if (datetime.strptime(now, "%Y-%m-%d %H:%M:%S") - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds() < 5 * 3600:
                continue
        except (ValueError, TypeError):
            pass
        set_env("last-auto-exchange", now, brand.brand_id)
        jobs.append(brand)

    def _fetch(brand):
        try:
            url = f"https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/{brand.currency_code.lower()}.json"
            data = requests.get(url, timeout=10).json()
            rates = data.get(brand.currency_code.lower(), {})
            for code, rate in rates.items():
                if code == brand.currency_code.lower() or not rate or rate <= 0:
                    continue
                converted = round(1 / rate, 4)
                Currency.objects.filter(brand_id=brand.brand_id, code=code).update(rate=converted)
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(_fetch, jobs))


def reconcile_balances() -> None:
    for rule in BalanceVerification.objects.filter(status="active"):
        reconcile_by_longest_chain(rule.device_id, rule.sender_key, rule.type)


def retry_webhooks() -> None:
    limit_raw = get_env("geneal-application-settings-webhook_attempts_limit")
    limit = int(limit_raw) if str(limit_raw).isdigit() else 1
    pendings = list(WebhookLog.objects.filter(status="pending", attempts__lt=limit).order_by("id")[:15])
    jobs = []
    for row in pendings:
        row.attempts += 1
        row.save(update_fields=["attempts", "updated_date"])
        try:
            payload = json.loads(row.payload or "{}")
        except ValueError:
            payload = {}
        jobs.append({"id": row.id, "url": row.url, "payload": payload, "attempts": row.attempts})
    results = send_ipn_multi(jobs)
    for job in jobs:
        code = results.get(job["id"], 0)
        row = next(r for r in pendings if r.id == job["id"])
        if code == 200:
            row.status = "completed"
        elif job["attempts"] >= limit:
            row.status = "canceled"
        else:
            row.status = "pending"
        row.http_code = str(code)
        row.save()


def run_cron() -> dict:
    if not acquire_lock():
        return {"status": "locked"}
    try:
        set_env("last-cron-invocation", M.get_current_datetime())
        check_updates()
        verified = verify_pending()
        refresh_currency()
        reconcile_balances()
        retry_webhooks()
        return {"status": "true", "message": "Cron run executed.", "verified": len(verified)}
    finally:
        release_lock()
