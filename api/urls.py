from django.urls import path
from api import views

urlpatterns = [
    path("checkout/redirect/", views.checkout_redirect, name="api-checkout-redirect"),
    path("checkout/redirect", views.checkout_redirect),
    path("checkout/popup/", views.checkout_popup, name="api-checkout-popup"),
    path("checkout/popup", views.checkout_popup),
    path("verify-payment/", views.verify_payment, name="api-verify"),
    path("verify-payment", views.verify_payment),
    path("refund-payment/", views.refund_payment, name="api-refund"),
    path("refund-payment", views.refund_payment),
]
