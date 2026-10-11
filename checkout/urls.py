from django.urls import path, re_path
from checkout import views

urlpatterns = [
    path("payment/<str:pp_id>/verify/", views.verify_endpoint, name="checkout-verify"),
    path("payment/<str:pp_id>/callback/", views.checkout_callback, name="checkout-callback"),
    path("payment/<str:pp_id>/receipt/", views.receipt_pdf, name="checkout-receipt"),
    path("payment/<str:pp_id>/", views.checkout_payment, name="checkout-payment"),
    path("invoice/webhook/", views.invoice_page, {"ref": "webhook"}, name="invoice-webhook"),
    path("invoice/webhook", views.invoice_page, {"ref": "webhook"}),
    path("invoice/<str:ref>/pay/", views.invoice_pay, name="invoice-pay"),
    path("invoice/<str:ref>/", views.invoice_page, name="invoice-page"),
    path("payment-link/default/<str:brand_id>/submit/", views.payment_link_default_submit,
         name="payment-link-default-submit"),
    path("payment-link/<str:ref>/submit/", views.payment_link_submit, name="payment-link-submit"),
    path("payment-link/<str:ref>/<str:brand_id>/", views.payment_link_page, name="payment-link-brand"),
    path("payment-link/<str:ref>/", views.payment_link_page, name="payment-link"),
]

# PHP ignores URL segments past the id (segments[2+] dropped): serve the
# same views for trailing paths. Listed last so specific routes win.
urlpatterns += [
    re_path(r"^payment/(?P<pp_id>[^/]+)/(.+/?)$", views.checkout_payment, name="checkout-tolerant"),
    re_path(r"^invoice/(?P<ref>[^/]+)/(.+/?)$", views.invoice_page, name="invoice-tolerant"),
    re_path(r"^payment-link/(?P<ref>[^/]+)/(.+/?)$", views.payment_link_page, name="link-tolerant"),
]
