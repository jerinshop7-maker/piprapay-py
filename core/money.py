"""
Port of pp-functions.php money helpers (bcscale(8)) + misc utilities.
All amounts are Decimal quantized to 8 places internally, rounded to 2 for display.
"""
from __future__ import annotations

import json
import random
import re
import secrets
import string
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

SCALE = 8
_Q8 = Decimal(1).scaleb(-8)
_Q2 = Decimal(1).scaleb(-2)


def _q(value: Decimal, scale: int = SCALE) -> Decimal:
    quantum = Decimal(1).scaleb(-scale)
    return value.quantize(quantum)


def money_sanitize(value) -> str:
    if value is None or value == "":
        return "0"
    s = str(value).replace(",", "").strip()
    if s in ("", "--"):
        return "0"
    try:
        d = Decimal(s)
    except InvalidOperation:
        return "0"
    return format(_q(d), "f")


def money_add(a, b, scale: int = SCALE) -> str:
    return format(_q(Decimal(money_sanitize(a)) + Decimal(money_sanitize(b)), scale), "f")


def money_sub(a, b, scale: int = SCALE) -> str:
    return format(_q(Decimal(money_sanitize(a)) - Decimal(money_sanitize(b)), scale), "f")


def money_mul(a, b, scale: int = SCALE) -> str:
    return format(_q(Decimal(money_sanitize(a)) * Decimal(money_sanitize(b)), scale), "f")


def money_div(a, b, scale: int = SCALE) -> str:
    divisor = Decimal(money_sanitize(b))
    if divisor == 0:
        return format(_q(Decimal(0), scale), "f")
    return format(_q(Decimal(money_sanitize(a)) / divisor, scale), "f")


def money_round(amount, decimals: int = 2) -> str:
    try:
        d = Decimal(str(amount).replace(",", ""))
    except InvalidOperation:
        d = Decimal(0)
    quantum = Decimal(1).scaleb(-decimals)
    return format(d.quantize(quantum, rounding=ROUND_HALF_UP), "f")


def money_to_int(amount, decimals: int = 2) -> int:
    d = Decimal(money_round(amount, decimals))
    return int((d * (10 ** decimals)).to_integral_value(rounding=ROUND_HALF_UP))


def int_to_money(amount: int, decimals: int = 2) -> str:
    return money_round(Decimal(amount) / (10 ** decimals), decimals)


def verify_payment_tolerance(checkout: str, paid: str, tolerance: str) -> bool:
    """Mirrors verifyPaymentTolerance(): |checkout - paid| <= tolerance."""
    try:
        diff = abs(Decimal(money_sanitize(checkout)) - Decimal(money_sanitize(paid)))
        return diff <= Decimal(money_sanitize(tolerance or "0"))
    except InvalidOperation:
        return False


def generate_item_id(length: int = 10, max_length: int = 10) -> str:
    length = min(length, max_length)
    alphabet = string.ascii_uppercase + string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_strong_password(length: int = 8) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def get_name_chars(full_name: str, length: int = 2) -> str:
    parts = [p for p in full_name.strip().split() if p]
    if not parts:
        return "PP"
    if len(parts) == 1:
        return parts[0][:length].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def get_current_datetime(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return datetime.now(timezone.utc).strftime(fmt)


def convert_utc_to_user_tz(utc_time, user_tz: str = "UTC", fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    try:
        if isinstance(utc_time, str):
            dt = datetime.strptime(utc_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        else:
            dt = utc_time
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo(user_tz)).strftime(fmt)
    except Exception:
        return str(utc_time)


def is_expired(expired_date) -> bool:
    if not expired_date or expired_date == "--":
        return False
    try:
        if isinstance(expired_date, str):
            dt = datetime.strptime(expired_date, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        else:
            dt = expired_date
        return datetime.now(timezone.utc) > dt
    except Exception:
        return False


def get_domain_value(url: str):
    from urllib.parse import urlparse

    try:
        host = urlparse(url).hostname
        return host or False
    except Exception:
        return False


def add_query_params(url: str, params: dict) -> str:
    from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

    parts = urlparse(url)
    query = dict(parse_qsl(parts.query))
    query.update({k: v for k, v in params.items()})
    return urlunparse(parts._replace(query=urlencode(query)))


def hex_to_rgba(hex_color: str, opacity: float = 1.0) -> str:
    h = (hex_color or "").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        r, g, b = 0, 0, 0
    return f"rgba({r}, {g}, {b}, {opacity})"


def time_ago(dt) -> str:
    from django.utils import timezone as dj_tz

    try:
        now = dj_tz.now()
        if isinstance(dt, str):
            dt = datetime.strptime(dt, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        delta = now - dt
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return f"{seconds} seconds ago"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes} minutes ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours} hours ago"
        days = hours // 24
        return f"{days} days ago"
    except Exception:
        return "--"


def get_user_device_info(user_agent: str = "") -> dict:
    ua = (user_agent or "").lower()
    if "mobile" in ua or "android" in ua or "iphone" in ua:
        device = "mobile"
    elif "tablet" in ua or "ipad" in ua:
        device = "tablet"
    else:
        device = "desktop"
    if "chrome" in ua:
        browser = "chrome"
    elif "firefox" in ua:
        browser = "firefox"
    elif "safari" in ua:
        browser = "safari"
    elif "edge" in ua:
        browser = "edge"
    else:
        browser = "other"
    return {"browser": browser, "device": device}
