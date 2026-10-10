from django.urls import path
from checkout import views

urlpatterns = [
    path("payment/<str:pp_id>/callback/", views.checkout_callback, name="checkout-callback"),
    path("payment/<str:pp_id>/receipt/", views.receipt_pdf, name="checkout-receipt"),
    path("payment/<str:pp_id>/", views.checkout_payment, name="checkout-payment"),
    path("invoice/<str:ref>/", views.invoice_page, name="invoice-page"),
    path("payment-link/<str:ref>/<str:brand_id>/", views.payment_link_page, name="payment-link-brand"),
    path("payment-link/<str:ref>/", views.payment_link_page, name="payment-link"),
]
