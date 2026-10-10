from django.contrib import admin as dj_admin
from core import models

for _name in ["Addon", "AddonParameter", "Admin", "ApiKey", "BalanceVerification", "Brand",
              "BrowserLog", "Currency", "Customer", "Device", "Domain", "EnvOption", "Faq",
              "Gateway", "GatewayParameter", "Invoice", "InvoiceItem", "PaymentLink",
              "PaymentLinkField", "Permission", "SmsData", "Transaction", "WebhookLog"]:
    try:
        dj_admin.site.register(getattr(models, _name))
    except Exception:
        pass
