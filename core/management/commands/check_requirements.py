"""Environment requirement check — mirrors pp-adapter.php requirements table
(PHP version -> Python version, curl -> requests, PDO -> DB backend, GD/fileinfo,
openssl -> ssl, zip, json, bcmath -> decimal). Exits non-zero when unmet."""
import ssl
import sys
import sqlite3

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Check runtime requirements (mirrors pp-requirement.php)."

    def handle(self, *args, **options):
        reqs = [
            ("Python Version", f"{sys.version_info.major}.{sys.version_info.minor}",
             "3.10 - 3.14", (3, 10) <= sys.version_info < (3, 15)),
        ]
        try:
            import requests  # noqa
            reqs.append(("requests (cURL)", "Enabled", "Enabled", True))
        except ImportError:
            reqs.append(("requests (cURL)", "Disabled", "Enabled", False))
        try:
            import PIL  # noqa
            reqs.append(("Pillow (GD/Imagick)", "Enabled", "Enabled", True))
        except ImportError:
            reqs.append(("Pillow (GD/Imagick)", "Disabled", "Enabled", False))
        try:
            import cryptography  # noqa
            reqs.append(("cryptography (OpenSSL/RSA)", "Enabled", "Enabled", True))
        except ImportError:
            reqs.append(("cryptography (OpenSSL/RSA)", "Disabled", "Enabled", False))
        reqs += [
            ("sqlite3 (PDO)", sqlite3.sqlite_version, "Enabled", True),
            ("ssl (OpenSSL)", ssl.OPENSSL_VERSION, "Enabled", True),
            ("zipfile (ZipArchive)", "Enabled", "Enabled", True),
            ("json", "Enabled", "Enabled", True),
            ("decimal (bcmath)", "Enabled", "Enabled", True),
        ]
        ok = True
        for name, current, required, passed in reqs:
            self.stdout.write(f"{'PASS' if passed else 'FAIL'} {name}: {current} (required {required})")
            ok = ok and passed
        if not ok:
            sys.exit(1)
        self.stdout.write(self.style.SUCCESS("All requirements met."))
