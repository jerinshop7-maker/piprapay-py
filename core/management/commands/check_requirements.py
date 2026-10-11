"""Environment requirement check — mirrors the 15-row pp-adapter.php table
(PHP version -> Python version, curl -> requests+pooling, PDO -> sqlite3,
GD+Imagick -> Pillow rows, OpenSSL -> cryptography+ssl, ZipArchive -> zipfile,
Mbstring/Tokenizer -> unicodedata/codecs notes, JSON -> json, allow_url_fopen
-> urllib, file_uploads, bcmath -> decimal). Exits non-zero when unmet."""
import codecs
import concurrent.futures
import mimetypes
import sqlite3
import ssl
import sys
import unicodedata
import urllib.request

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
            reqs.append(("cURL", "Enabled", "Enabled", True))
        except ImportError:
            reqs.append(("cURL", "Disabled", "Enabled", False))
        reqs.append(("cURL Multi", "Enabled" if hasattr(concurrent.futures, "ThreadPoolExecutor")
                     else "Disabled", "Enabled",
                     hasattr(concurrent.futures, "ThreadPoolExecutor")))
        try:
            import PIL  # noqa
            reqs.append(("GD Library", "Enabled", "Enabled", True))
            reqs.append(("Imagick", "Enabled (Pillow)", "Enabled", True))
        except ImportError:
            reqs.append(("GD Library", "Disabled", "Enabled", False))
            reqs.append(("Imagick", "Disabled", "Enabled", False))
        try:
            import cryptography  # noqa
            reqs.append(("OpenSSL", "Enabled", "Enabled", True))
        except ImportError:
            reqs.append(("OpenSSL", "Disabled", "Enabled", False))
        reqs += [
            ("PDO", sqlite3.sqlite_version, "Enabled", True),
            ("Mbstring", unicodedata.unidata_version, "Enabled", True),
            ("Tokenizer", "Enabled" if hasattr(codecs, "decode") else "Disabled",
             "Enabled", hasattr(codecs, "decode")),
            ("JSON", "Enabled", "Enabled", True),
            ("allow_url_fopen", "Enabled" if hasattr(urllib.request, "urlopen") else "Disabled",
             "Enabled", hasattr(urllib.request, "urlopen")),
            ("file_uploads", "Enabled", "Enabled", True),
            ("Fileinfo", "Enabled" if mimetypes.guess_type("x.jpg")[0] else "Disabled",
             "Enabled", bool(mimetypes.guess_type("x.jpg")[0])),
            ("ZipArchive", "Enabled", "Enabled", True),
            ("bcmath", "Enabled", "Enabled", True),
        ]
        try:
            import bcrypt  # noqa
            reqs.append(("BCRYPT passwords", "Enabled", "Enabled", True))
        except ImportError:
            reqs.append(("BCRYPT passwords", "Disabled", "Enabled", False))
        ok = True
        for name, current, required, passed in reqs:
            self.stdout.write(f"{'PASS' if passed else 'FAIL'} {name}: {current} (required {required})")
            ok = ok and passed
        if not ok:
            sys.exit(1)
        self.stdout.write(self.style.SUCCESS("All requirements met."))
