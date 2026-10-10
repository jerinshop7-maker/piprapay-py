"""PipraPay Django port — settings. Mirrors pp-config.php + pp_env table + pp-adapter.php requirements."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-piprapay-port-dev-key-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
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
PIPRAPAY_VERSION = {"version_name": "1.0.0-django", "version_code": "100"}
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
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": os.environ.get("DB_ENGINE", "django.db.backends.sqlite3"),
        "NAME": os.environ.get("DB_NAME", str(BASE_DIR / "db.sqlite3")),
        "USER": os.environ.get("DB_USER", ""),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", ""),
        "PORT": os.environ.get("DB_PORT", ""),
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

SESSION_COOKIE_AGE = 365 * 24 * 3600
SESSION_COOKIE_HTTPONLY = True

# File-upload limits mirror pp-functions.php uploadImage()
PIPRAPAY_MAX_IMAGE_MB = 5
PIPRAPAY_ALLOWED_IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif"}
