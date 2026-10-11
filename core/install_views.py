"""Web installer — mirrors pp-content/pp-install/index.php (requirements →
database note → admin → complete). Serves only while no Admin row exists
(PipraPayInstallMiddleware enforces the gate)."""
import json
import sys

from django.contrib.auth.hashers import make_password
from django.core.management import call_command
from django.shortcuts import redirect, render

from core.env import set_env
from core.models import Admin, Brand, Currency, Permission
from core.money import generate_item_id, generate_strong_password
from core.permissions import permission_schema


def _requirement_rows():
    import concurrent.futures
    import mimetypes
    import sqlite3
    import ssl
    import codecs
    import urllib.request
    rows = []
    rows.append({"name": "Python Version", "required": "3.10 - 3.14",
                 "current": f"{sys.version_info.major}.{sys.version_info.minor}",
                 "ok": (3, 10) <= sys.version_info < (3, 15)})
    try:
        import requests  # noqa
        rows.append({"name": "cURL", "required": "Enabled", "current": "Enabled", "ok": True})
    except ImportError:
        rows.append({"name": "cURL", "required": "Enabled", "current": "Disabled", "ok": False})
    rows.append({"name": "cURL Multi", "required": "Enabled", "current": "Enabled",
                 "ok": hasattr(concurrent.futures, "ThreadPoolExecutor")})
    try:
        import PIL  # noqa
        rows.append({"name": "GD Library", "required": "Enabled", "current": "Enabled", "ok": True})
        rows.append({"name": "Imagick", "required": "Enabled", "current": "Enabled (Pillow)", "ok": True})
    except ImportError:
        rows.append({"name": "GD Library", "required": "Enabled", "current": "Disabled", "ok": False})
        rows.append({"name": "Imagick", "required": "Enabled", "current": "Disabled", "ok": False})
    try:
        import cryptography  # noqa
        rows.append({"name": "OpenSSL", "required": "Enabled", "current": ssl.OPENSSL_VERSION, "ok": True})
    except ImportError:
        rows.append({"name": "OpenSSL", "required": "Enabled", "current": "Disabled", "ok": False})
    rows.append({"name": "PDO", "required": "Enabled", "current": sqlite3.sqlite_version, "ok": True})
    import unicodedata
    rows.append({"name": "Mbstring", "required": "Enabled", "current": unicodedata.unidata_version, "ok": True})
    rows.append({"name": "Tokenizer", "required": "Enabled", "current": "Enabled",
                 "ok": hasattr(codecs, "decode")})
    rows.append({"name": "JSON", "required": "Enabled", "current": "Enabled", "ok": True})
    rows.append({"name": "allow_url_fopen", "required": "Enabled", "current": "Enabled",
                 "ok": hasattr(urllib.request, "urlopen")})
    rows.append({"name": "file_uploads", "required": "Enabled", "current": "Enabled", "ok": True})
    rows.append({"name": "Fileinfo", "required": "Enabled",
                 "current": "Enabled" if mimetypes.guess_type("x.jpg")[0] else "Disabled",
                 "ok": bool(mimetypes.guess_type("x.jpg")[0])})
    rows.append({"name": "ZipArchive", "required": "Enabled", "current": "Enabled", "ok": True})
    rows.append({"name": "bcmath", "required": "Enabled", "current": "Enabled (decimal)", "ok": True})
    return rows


def install_index(request):
    if Admin.objects.exists():
        return redirect("/login/")
    return render(request, "core/install.html", {"step": "requirements"})


def install_requirements(request):
    if Admin.objects.exists():
        return redirect("/login/")
    rows = _requirement_rows()
    ok = all(r["ok"] for r in rows)
    return render(request, "core/install.html",
                  {"step": "requirements", "rows": rows, "ok": ok})


def install_admin(request):
    if Admin.objects.exists():
        return redirect("/login/")
    if request.method == "POST":
        name = (request.POST.get("full_name") or "").strip()
        username = (request.POST.get("username") or "").strip()
        email = (request.POST.get("email") or "").strip()
        pw1, pw2 = request.POST.get("password", ""), request.POST.get("password_confirm", "")
        if not name or not username or not email or not pw1:
            return render(request, "core/install.html",
                          {"step": "admin", "error": "Please fill in all required fields."})
        if "@" not in email:
            return render(request, "core/install.html",
                          {"step": "admin", "error": "Please enter a valid email address."})
        if pw1 != pw2:
            return render(request, "core/install.html",
                          {"step": "admin", "error": "Passwords do not match."})
        try:
            call_command("migrate", run_syncdb=True, verbosity=0)
        except Exception:
            pass
        a_id = generate_item_id()
        Admin.objects.create(a_id=a_id, full_name=name, username=username, email=email,
                             password=make_password(pw1),
                             temp_password=make_password(generate_strong_password(8)),
                             role="admin", status="active")
        brand_id = generate_item_id()
        Brand.objects.create(brand_id=brand_id, identify_name="Default", name="Default",
                             currency_code="BDT")
        Permission.objects.create(brand_id=brand_id, a_id=a_id,
                                  permission=json.dumps(permission_schema()))
        Currency.objects.create(brand_id=brand_id, code="BDT", symbol="৳", rate=1)
        set_env("cron-job", generate_item_id())
        return render(request, "core/install.html", {"step": "complete"})
    return render(request, "core/install.html", {"step": "admin"})
