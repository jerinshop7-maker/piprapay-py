"""Installer — mirrors pp-content/pp-install/index.php steps 2-4.

Step 1 (database) is `python manage.py migrate` (Django owns the schema, same
23 pp_* tables). This command seeds: admin (BCRYPT via Django hasher) +
permission row (full schema) + default brand + BDT currency, then prints the
dashboard login target. Interactive; --noinput accepts env/defaults.
"""
import getpass

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand

from core.env import set_env
from core.models import Admin, Brand, Currency, Permission
from core.money import generate_item_id, generate_strong_password
from core.permissions import permission_schema
import json


class Command(BaseCommand):
    help = "Seed initial admin + brand + currency (mirrors pp-install wizard)."

    def add_arguments(self, parser):
        parser.add_argument("--noinput", action="store_true")
        parser.add_argument("--username", default="admin")
        parser.add_argument("--email", default="admin@example.com")
        parser.add_argument("--name", default="Administrator")

    def handle(self, *args, **options):
        if Admin.objects.exists():
            self.stdout.write("Admin already exists — nothing to do.")
            return
        if options["noinput"]:
            username, email, name, password = (options["username"], options["email"],
                                              options["name"], generate_strong_password(12))
        else:
            username = input("Admin username [admin]: ") or "admin"
            email = input(f"Admin email [{options['email']}]: ") or options["email"]
            name = input("Full name [Administrator]: ") or "Administrator"
            password = getpass.getpass("Password (blank = generate): ") or generate_strong_password(12)
        a_id = generate_item_id()
        admin = Admin.objects.create(a_id=a_id, full_name=name, username=username, email=email,
                                     password=make_password(password),
                                     temp_password=make_password(generate_strong_password(8)),
                                     role="admin", status="active")
        brand_id = generate_item_id()
        Brand.objects.create(brand_id=brand_id, identify_name="Default", name="Default",
                             currency_code="BDT")
        Permission.objects.create(brand_id=brand_id, a_id=a_id,
                                  permission=json.dumps(permission_schema()))
        Currency.objects.create(brand_id=brand_id, code="BDT", symbol="৳", rate=1)
        set_env("cron-job", generate_item_id())
        self.stdout.write(self.style.SUCCESS(
            f"Installed. Login at /login/ as '{username}'."))
