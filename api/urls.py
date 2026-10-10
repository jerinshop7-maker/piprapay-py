from django.urls import path, re_path
from api import companion, views

urlpatterns = [
    path("checkout/redirect/", views.checkout_redirect, name="api-checkout-redirect"),
    path("checkout/redirect", views.checkout_redirect),
    path("checkout/popup/", views.checkout_popup, name="api-checkout-popup"),
    path("checkout/popup", views.checkout_popup),
    path("verify-payment/", views.verify_payment, name="api-verify"),
    path("verify-payment", views.verify_payment),
    path("refund-payment/", views.refund_payment, name="api-refund"),
    path("refund-payment", views.refund_payment),
    # Android companion-device API (action-companion).
    path("companion/<str:action>/", companion.companion, name="api-companion"),
    path("", views.unknown_api, name="api-root"),
    # Unknown api_type/checkout_type -> JSON INVALID_JSON_PAYLOAD (not HTML 404).
    re_path(r"^(?P<path>.*)/$", views.unknown_api, name="api-unknown"),
    re_path(r"^(?P<path>.+)$", views.unknown_api),
]
