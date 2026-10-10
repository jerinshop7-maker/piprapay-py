from django.urls import path
from gateways import ipn_views

urlpatterns = [
    path("", ipn_views.ipn_entry, name="gateway-ipn"),
]
