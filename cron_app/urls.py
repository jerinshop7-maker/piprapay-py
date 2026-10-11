from django.urls import path, re_path
from cron_app import views

urlpatterns = [
    path("<str:token>/", views.cron_entry, name="cron-run"),
    re_path(r"^(?P<token>[^/]+)/(.+/?)$", views.cron_entry, name="cron-tolerant"),
]
