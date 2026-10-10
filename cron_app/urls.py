from django.urls import path
from cron_app import views

urlpatterns = [
    path("<str:token>/", views.cron_entry, name="cron-run"),
]
