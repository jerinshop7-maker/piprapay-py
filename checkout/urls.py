from django.urls import path
from checkout import views

urlpatterns = [
    path("payment/<str:pp_id>/verify/", views.verify_endpoint, name="checkout-verify"),
    path("payment/<str:pp_id>/callback/", views.checkout_callback, name="checkout-callback"),
    path("payment/<str:pp_id>/receipt/", views.receipt_pdf, name="checkout-receipt"),
    path("payment/<str:pp_id>/", views.checkout_payment, name="checkout-payment"),
    path("invoice/webhook/", views.invoice_page, name="invoice-webhook"),
    path("invoice/webhook", views.invoice_page),
    path("invoice/<str:ref>/pay/", views.invoice_pay, name="invoice-pay"),
    path("invoice/<str:ref>/", views.invoice_page, name="invoice-page"),
    path("payment-link/default/<str:brand_id>/submit/", views.payment_link_default_submit,
         name="payment-link-default-submit"),
    path("payment-link/<str:ref>/submit/", views.payment_link_submit, name="payment-link-submit"),
    path("payment-link/<str:ref>/<str:brand_id>/", views.payment_link_page, name="payment-link-brand"),
    path("payment-link/<str:ref>/", views.payment_link_page, name="payment-link"),
]
