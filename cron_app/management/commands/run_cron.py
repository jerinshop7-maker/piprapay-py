from django.core.management.base import BaseCommand
from cron_app.tasks import run_cron


class Command(BaseCommand):
    help = "Run PipraPay cron (pending verify, currency refresh, webhooks). Mirrors cron/<token>."

    def handle(self, *args, **options):
        result = run_cron()
        self.stdout.write(str(result))
