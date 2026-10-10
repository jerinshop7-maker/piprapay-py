"""HTTP entry for cron/<token> — token must equal env 'cron-job'."""
from django.http import JsonResponse
from core.env import get_env
from cron_app.tasks import run_cron


def cron_entry(request, token: str):
    if not token or token != get_env("cron-job"):
        return JsonResponse({"error": "not found"}, status=404)
    return JsonResponse(run_cron())
