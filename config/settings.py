"""PipraPay Django port — settings. Mirrors pp-config.php + pp_env table + pp-adapter.php requirements."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-piprapay-port-dev-key-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

# PipraPay path slugs — must stay identical to pp-adapter.php
PIPRAPAY_PATHS = {
    "payment": "payment",
    "invoice": "invoice",
    "payment_link": "payment-link",
    "admin": "admin",
    "cron": "cron",
    "homepage_redirect": "",
}
PIPRAPAY_VERSION = {"version_name": "v3.0.0-beta", "version_code": "3.0.0", "channel": "beta"}
PIPRAPAY_UPDATE_MANIFEST_URL = "https://updates.piprapay.com/manifest.json"

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "gateways",
    "checkout",
    "api",
    "staff",
    "cron_app",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "core.middleware.PipraPaySecurityHeadersMiddleware",
    "core.middleware.PipraPayMaintenanceMiddleware",
    "core.middleware.PipraPayLogoutMiddleware",
    "core.middleware.PipraPayLegacyPageMiddleware",
    "core.middleware.PipraPayInstallMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.messages.context_processors.messages",
                "staff.context_processors.pending_badge",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

_DB_ENGINE = os.environ.get("DB_ENGINE", "django.db.backends.sqlite3")
DATABASES = {
    "default": {
        "ENGINE": _DB_ENGINE,
        "NAME": os.environ.get("DB_NAME", str(BASE_DIR / "db.sqlite3")),
        "USER": os.environ.get("DB_USER", ""),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", ""),
        "PORT": os.environ.get("DB_PORT", ""),
        **({"OPTIONS": {"charset": "utf8mb4"}} if "mysql" in _DB_ENGINE else {}),
    }
}

TIME_ZONE = "UTC"
USE_TZ = True

LANGUAGE_CODE = "en-us"

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# PHP uses password_hash/password_verify with PASSWORD_BCRYPT; bcrypt first so
# legacy rows verify and new hashes are bcrypt (PBKDF2 kept as fallback).
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

SESSION_COOKIE_AGE = 365 * 24 * 3600
SESSION_COOKIE_HTTPONLY = True

# File-upload limits mirror pp-functions.php uploadImage()
PIPRAPAY_MAX_IMAGE_MB = 5
PIPRAPAY_ALLOWED_IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif"}
