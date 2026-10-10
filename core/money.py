"""
Exact port of pp-functions.php money + misc helpers.

PHP semantics preserved precisely (verified against source):
- money_sanitize(): is_numeric() else "0" — NO comma stripping, NO normalization.
- money_round(): bc-truncation (NOT half-up). 10.005 -> "10.00".
- verify_payment_tolerance(): rounds to 2dp, requires checkout>0 AND paid>0,
  accepts paid in [checkout, checkout+tolerance]. Underpayments NEVER pass.
- generate_item_id(): DIGITS ONLY (mt_rand(0,9) in PHP; secrets here).
- is_expired(): empty/-- -> False; unparseable -> True; date-only -> EOD 23:59:59.
- get_domain_value(): prepends http://, strips www., strict hostname regex, lower.
- add_query_params(): drops fragment/port, ALWAYS appends '?'.
- time_ago(): brand-TZ y/m/d/h/i + "Just now".
- get_user_device_info(): {ip_address, device, os, browser} exact PHP strings.
- get_name_chars(): first + last[0:length-1], upper; '' when empty.
"""
from __future__ import annotations

import re
import secrets
import string
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from zoneinfo import ZoneInfo

SCALE = 8


def _q(value: Decimal, scale: int = SCALE) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_DOWN)


def money_sanitize(value) -> str:
    """is_numeric($value) ? (string)$value : "0"."""
    if value is None:
        return "0"
    if isinstance(value, bool):
        return "0"
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    s = str(value).strip()
    if s in ("", "--"):
        return "0"
    try:
        Decimal(s)
    except InvalidOperation:
        return "0"
    # is_numeric("  12  ")? PHP is_numeric allows leading/trailing whitespace -> numeric.
    # Mirror: accept iff Decimal parses.
    return s


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


def num_eq(a, b) -> bool:
    """PHP `==` numeric juggling for amount strings ("100.50000000" == "100.50")."""
    try:
        return Decimal(money_sanitize(a)) == Decimal(money_sanitize(b))
    except InvalidOperation:
        return False


def money_round(amount, decimals: int = 2) -> str:
    """bc-truncation: bcmul(x,10^(d+1),0) -> bcdiv(/10,0) -> bcdiv(/10^d,d)."""
    sanitized = money_sanitize(amount)
    try:
        d = Decimal(sanitized)
    except InvalidOperation:
        d = Decimal(0)
    factor = Decimal(10) ** (decimals + 1)
    tmp = (d * factor).to_integral_value(rounding=ROUND_DOWN)
    tmp = (tmp / 10).to_integral_value(rounding=ROUND_DOWN)
    result = (tmp / (Decimal(10) ** decimals)).quantize(
        Decimal(1).scaleb(-decimals), rounding=ROUND_DOWN)
    return format(result, "f")


def money_to_int(amount, decimals: int = 2) -> int:
    """bcmul(sanitize, 10^dec, 0) — truncation."""
    try:
        d = Decimal(money_sanitize(amount))
    except InvalidOperation:
        d = Decimal(0)
    return int((d * (Decimal(10) ** decimals)).to_integral_value(rounding=ROUND_DOWN))


def int_to_money(amount: int, decimals: int = 2) -> str:
    """bcdiv(a, 10^dec, dec)."""
    result = (Decimal(int(amount)) / (Decimal(10) ** decimals)).quantize(
        Decimal(1).scaleb(-decimals), rounding=ROUND_DOWN)
    return format(result, "f")


def verify_payment_tolerance(checkout: str, paid: str, tolerance: str) -> bool:
    """paid must lie in [checkout, checkout+tolerance]; both must be > 0."""
    checkout_r = money_round(checkout)
    paid_r = money_round(paid)
    tolerance_r = money_round(tolerance)
    try:
        c, p, t = Decimal(checkout_r), Decimal(paid_r), Decimal(tolerance_r)
    except InvalidOperation:
        return False
    if c <= 0 or p <= 0:
        return False
    max_allowed = c + t
    return p >= c and p <= max_allowed


def generate_item_id(length: int = 10, max_length: int = 10) -> str:
    """Digits only — mirrors mt_rand(0,9) loop (CSPRNG via secrets)."""
    length = min(length, max_length)
    return "".join(secrets.choice(string.digits) for _ in range(length))


def generate_strong_password(length: int = 8) -> str:
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789@#$%&!"
    return "".join(secrets.choice(chars) for _ in range(length))


def get_name_chars(full_name: str, length: int = 2) -> str:
    full_name = (full_name or "").strip()
    if not full_name or length <= 0:
        return ""
    parts = [p for p in full_name.split(" ") if p]
    if len(parts) > 1:
        return (parts[0][0] + parts[-1][: max(0, length - 1)]).upper()
    return parts[0][:length].upper()


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
    s = str(expired_date).strip()
    if re.match(r"^\d{1,4}[-/]\d{1,2}[-/]\d{1,4}$", s):
        try:
            dt = datetime.strptime(s.replace("/", "-"), "%Y-%m-%d")
            s = dt.strftime("%Y-%m-%d 23:59:59")
        except ValueError:
            return True
    try:
        ts = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        try:
            ts = datetime.fromisoformat(s).timestamp()
        except ValueError:
            return True
    return datetime.now(timezone.utc).timestamp() > ts


_HOST_RE = re.compile(r"^(?!-)(?:[a-z0-9-]{1,63}\.)+[a-z]{2,}$", re.IGNORECASE)


def get_domain_value(url: str):
    """Mirror getDomainValue(): scheme default, strip www., strict host regex, lower."""
    s = (url or "").strip()
    if not s:
        return False
    if not re.match(r"^https?://", s, re.IGNORECASE):
        s = "http://" + s
    from urllib.parse import urlparse

    try:
        host = urlparse(s).hostname
    except Exception:
        return False
    if not host:
        return False
    host = re.sub(r"^www\.", "", host, flags=re.IGNORECASE)
    if not _HOST_RE.match(host):
        return False
    return host.lower()


def add_query_params(url: str, params: dict | None = None) -> str:
    """Mirror addQueryParams(): drops fragment/port, always appends '?'."""
    from urllib.parse import parse_qsl, urlencode, urlparse

    params = params or {}
    parts = urlparse(url or "")
    existing = dict(parse_qsl(parts.query or ""))
    merged = {**existing, **params}
    query = urlencode(merged)
    base = ""
    if parts.scheme:
        base += parts.scheme + "://"
    base += parts.hostname or ""
    base += parts.path or ""
    return base + "?" + query


def hex_to_rgba(hex_color: str, opacity: float = 1.0) -> str:
    h = (hex_color or "").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        r, g, b = 0, 0, 0
    return f"rgba({r}, {g}, {b}, {opacity})"


def time_ago(dt, brand_timezone: str = "Asia/Dhaka") -> str:
    """Mirror timeAgo(): brand-TZ diff with y/m/d/h/i, else 'Just now'."""
    try:
        tz = ZoneInfo(brand_timezone or "Asia/Dhaka")
    except Exception:
        tz = ZoneInfo("Asia/Dhaka")
    try:
        if isinstance(dt, str):
            past = datetime.strptime(dt, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone(tz)
        else:
            past = dt.astimezone(tz) if dt.tzinfo else dt.replace(tzinfo=timezone.utc).astimezone(tz)
        now = datetime.now(tz)
        delta = now - past
        days = delta.days
        seconds = delta.seconds
        years, days = divmod(days, 365)
        months, days = divmod(days, 30)
        hours, rem = divmod(seconds, 3600)
        minutes, _ = divmod(rem, 60)
        if years > 0:
            return f"{years} year{'s' if years > 1 else ''} ago"
        if months > 0:
            return f"{months} month{'s' if months > 1 else ''} ago"
        if days > 0:
            return f"{days} day{'s' if days > 1 else ''} ago"
        if hours > 0:
            return f"{hours} hour{'s' if hours > 1 else ''} ago"
        if minutes > 0:
            return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
        return "Just now"
    except Exception:
        return "--"


_MOBILE_RE = re.compile(r"mobile", re.IGNORECASE)
_TABLET_RE = re.compile(r"tablet", re.IGNORECASE)


def get_user_device_info(request=None, user_agent: str = "", ip: str = "Unknown") -> dict:
    """Mirror getUserDeviceInfo(): {ip_address, device, os, browser}."""
    if request is not None:
        user_agent = request.META.get("HTTP_USER_AGENT", "Unknown")
        ip = request.META.get("REMOTE_ADDR", "Unknown")
    ua = user_agent or "Unknown"
    if _MOBILE_RE.search(ua):
        device = "Mobile"
    elif _TABLET_RE.search(ua):
        device = "Tablet"
    else:
        device = "Desktop"
    if re.search(r"Windows", ua):
        os = "Windows"
    elif re.search(r"Mac", ua):
        os = "Mac OS"
    elif re.search(r"Linux", ua):
        os = "Linux"
    elif re.search(r"Android", ua):
        os = "Android"
    elif re.search(r"iPhone|iPad", ua):
        os = "iOS"
    else:
        os = "Unknown OS"
    if re.search(r"MSIE|Trident", ua):
        browser = "Internet Explorer"
    elif re.search(r"Firefox", ua):
        browser = "Firefox"
    elif re.search(r"Chrome", ua):
        browser = "Chrome"
    elif re.search(r"Safari", ua):
        browser = "Safari"
    elif re.search(r"Opera|OPR", ua):
        browser = "Opera"
    elif re.search(r"Edge", ua):
        browser = "Edge"
    else:
        browser = "Unknown Browser"
    return {"ip_address": ip, "device": device, "os": os, "browser": browser}


def dateformat(value: str, fmt: str = "%Y-%m-%d") -> bool:
    """Mirror dateformat(): strict format equality check -> bool."""
    try:
        return datetime.strptime(str(value), fmt).strftime(fmt) == str(value)
    except (ValueError, TypeError):
        return False


def get_param(params: dict, key: str):
    """Mirror getParam(): trimmed [a-zA-Z0-9_-]+ allowlist or None."""
    v = params.get(key)
    if v is None:
        return None
    v = str(v).strip()
    return v if re.fullmatch(r"[a-zA-Z0-9_-]+", v) else None


def site_url(request=None) -> str:
    """Mirror pp_site_address(): absolute base URL with trailing slash."""
    if request is None:
        return "/"
    base = request.build_absolute_uri("/") 
    return base if base.endswith("/") else base + "/"
