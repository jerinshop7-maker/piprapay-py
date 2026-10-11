"""
All 23 tables from pp-content/pp-install/db.sql ported 1:1 to Django ORM.

PHP `pp_*` prefix is dropped; `Meta.db_table` keeps the exact legacy table
name. NOTE: this is NOT a drop-in for an existing PipraPay MySQL database —
upstream stores `created_date`/`updated_date` as `varchar(20)` while these
models use `DateTimeField`, so a legacy DB needs an import/type-migration step
(see reports/schema-parity.md).

Conventions mirrored from PHP:
- datetimes stored as UTC strings 'Y-m-d H:i:s' in PHP; here we use
  auto-managed CharField-compatible DateTimeFields in UTC (USE_TZ=True).
  `created_date`/`updated_date` are kept as DateTimeField for sane querying;
  legacy string values import cleanly via the import command.
- money columns were decimal(20,8); we keep Decimal(max_digits=20,
  decimal_places=8) and do all math with core.money helpers (bcscale(8)).
- '--' sentinel for "empty" is preserved as default on the same columns.
"""
from django.db import models


class Addon(models.Model):
    addon_id = models.CharField(max_length=15, db_index=True)
    slug = models.CharField(max_length=40, default="--")
    name = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=8, default="active")  # active|inactive
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pp_addon"
        indexes = [
            models.Index(fields=['slug']),
        ]


class AddonParameter(models.Model):
    addon_id = models.CharField(max_length=15, db_index=True)
    option_name = models.CharField(max_length=50)
    value = models.TextField()
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pp_addon_parameter"
        indexes = [
            models.Index(fields=['option_name']),
        ]


class Admin(models.Model):
    ROLE_CHOICES = (("admin", "admin"), ("staff", "staff"))
    a_id = models.CharField(max_length=15, db_index=True)
    full_name = models.TextField()
    username = models.CharField(max_length=50, unique=True)
    email = models.EmailField(max_length=100)
    password = models.TextField()  # Django password hash (PHP: password_hash)
    temp_password = models.TextField(null=True, blank=True)
    reset_limit = models.CharField(max_length=10, default="3")
    status = models.CharField(max_length=8, default="active")  # active|suspend
    role = models.CharField(max_length=8, default="admin", choices=ROLE_CHOICES)
    twofa_status = models.CharField(max_length=8, default="disable", db_column="2fa_status")
    twofa_secret = models.CharField(max_length=64, default="--", db_column="2fa_secret")
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pp_admin"


class ApiKey(models.Model):
    brand_id = models.CharField(max_length=15, db_index=True)
    name = models.TextField()
    api_key = models.CharField(max_length=60, unique=True)
    expired_date = models.TextField(null=True, blank=True)
    api_scopes = models.TextField()  # JSON list: create_payment|verify_payment|refund_payment
    status = models.CharField(max_length=8, default="active")
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pp_api"
        indexes = [
            models.Index(fields=['status']),
        ]


class BalanceVerification(models.Model):
    device_id = models.CharField(max_length=15)
    sender_key = models.CharField(max_length=15)
    type = models.CharField(max_length=10, default="Personal")  # Personal|Agent|Merchant
    current_balance = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    simslot = models.CharField(max_length=6)
    status = models.CharField(max_length=8, default="active")
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pp_balance_verification"
        indexes = [
            models.Index(fields=['device_id', 'sender_key', 'type']),
            models.Index(fields=['status']),
        ]


class Brand(models.Model):
    brand_id = models.CharField(max_length=15, unique=True)
    favicon = models.TextField(null=True, blank=True)
    logo = models.TextField(null=True, blank=True)
    identify_name = models.CharField(max_length=50, default="Default")
    name = models.TextField(null=True, blank=True)
    support_email_address = models.TextField(null=True, blank=True)
    support_phone_number = models.TextField(null=True, blank=True)
    support_website = models.TextField(null=True, blank=True)
    whatsapp_number = models.TextField(null=True, blank=True)
    telegram = models.TextField(null=True, blank=True)
    facebook_messenger = models.TextField(null=True, blank=True)
    facebook_page = models.TextField(null=True, blank=True)
    theme = models.CharField(max_length=120, default="twenty-six")
    street_address = models.TextField(null=True, blank=True)
    city_town = models.TextField(null=True, blank=True)
    postal_code = models.TextField(null=True, blank=True)
    country = models.TextField(null=True, blank=True)
    timezone = models.CharField(max_length=150, default="Asia/Dhaka")
    language = models.CharField(max_length=150, default="en")
    currency_code = models.CharField(max_length=150, default="BDT")
    autoExchange = models.CharField(max_length=8, default="disabled")
    payment_tolerance = models.CharField(max_length=150, default="0")
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pp_brands"

    def __str__(self):
        return self.name if self.name and self.name != "--" else self.identify_name


class BrowserLog(models.Model):
    a_id = models.CharField(max_length=15)
    cookie = models.CharField(max_length=40)
    browser = models.CharField(max_length=10)
    device = models.CharField(max_length=10)
    ip = models.CharField(max_length=15)
    status = models.CharField(max_length=8, default="active")
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pp_browser_log"
        indexes = [
            models.Index(fields=['a_id']),
            models.Index(fields=['cookie']),
            models.Index(fields=['status']),
        ]


class Currency(models.Model):
    brand_id = models.CharField(max_length=15, db_index=True)
    code = models.CharField(max_length=6)
    symbol = models.CharField(max_length=5)
    rate = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pp_currency"
        unique_together = ("brand_id", "code")


class Customer(models.Model):
    ref = models.CharField(max_length=15)
    brand_id = models.CharField(max_length=15, db_index=True)
    name = models.TextField()
    email = models.EmailField(max_length=100)
    mobile = models.CharField(max_length=15)
    status = models.CharField(max_length=8, default="active")
    suspend_reason = models.TextField(null=True, blank=True)
    inserted_via = models.CharField(max_length=8, default="manual")
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pp_customer"
        indexes = [
            models.Index(fields=['email']),
            models.Index(fields=['status']),
        ]


class Device(models.Model):
    d_id = models.CharField(max_length=40)
    device_id = models.CharField(max_length=15, db_index=True)
    otp = models.CharField(max_length=15)
    name = models.TextField(null=True, blank=True)
    model = models.TextField(null=True, blank=True)
    android_level = models.TextField(null=True, blank=True)
    app_version = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=10, default="processing")
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)
    last_sync = models.CharField(max_length=20, default="--")

    class Meta:
        db_table = "pp_device"
        indexes = [
            models.Index(fields=['d_id']),
            models.Index(fields=['otp']),
            models.Index(fields=['status']),
        ]


class Domain(models.Model):
    domain = models.CharField(max_length=50, unique=True)
    status = models.CharField(max_length=8, default="active")
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pp_domain"
        indexes = [
            models.Index(fields=['status']),
        ]


class EnvOption(models.Model):
    """pp_env key/value store. brand_id='both' = global (mirrors get_env/set_env)."""
    brand_id = models.CharField(max_length=15, default="both")
    option_name = models.CharField(max_length=50)
    value = models.TextField()
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pp_env"
        unique_together = ("brand_id", "option_name")


class Faq(models.Model):
    brand_id = models.CharField(max_length=15, db_index=True)
    title = models.TextField()
    description = models.TextField()
    status = models.CharField(max_length=8, default="active")
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pp_faq"
        indexes = [
            models.Index(fields=['status']),
        ]


class Gateway(models.Model):
    gateway_id = models.CharField(max_length=15, db_index=True)
    brand_id = models.CharField(max_length=15, db_index=True)
    slug = models.CharField(max_length=40, default="--")
    name = models.TextField(null=True, blank=True)
    display = models.TextField(null=True, blank=True)
    logo = models.TextField(null=True, blank=True)
    currency = models.CharField(max_length=6)
    min_allow = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    max_allow = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    fixed_discount = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    percentage_discount = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    fixed_charge = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    percentage_charge = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    primary_color = models.TextField(null=True, blank=True)
    text_color = models.TextField(null=True, blank=True)
    btn_color = models.TextField(null=True, blank=True)
    btn_text_color = models.TextField(null=True, blank=True)
    tab = models.CharField(max_length=6, default="mfs")  # mfs|bank|global
    status = models.CharField(max_length=8, default="active")
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pp_gateways"
        indexes = [
            models.Index(fields=['brand_id', 'slug']),
            models.Index(fields=['tab']),
            models.Index(fields=['status']),
        ]


class GatewayParameter(models.Model):
    brand_id = models.CharField(max_length=15)
    gateway_id = models.CharField(max_length=15, db_index=True)
    option_name = models.CharField(max_length=50)
    value = models.TextField()
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pp_gateways_parameter"
        indexes = [
            models.Index(fields=['gateway_id', 'option_name']),
        ]


class Invoice(models.Model):
    ref = models.CharField(max_length=30, unique=True)
    brand_id = models.CharField(max_length=15, db_index=True)
    customer_info = models.TextField(null=True, blank=True)  # JSON
    gateway_id = models.CharField(max_length=15, default="--")
    currency = models.TextField()
    due_date = models.TextField(null=True, blank=True)
    shipping = models.CharField(max_length=250, default="0")
    status = models.CharField(max_length=8, default="unpaid")
    note = models.TextField(null=True, blank=True)
    private_note = models.TextField(null=True, blank=True)
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pp_invoice"
        indexes = [
            models.Index(fields=['status']),
        ]


class InvoiceItem(models.Model):
    brand_id = models.CharField(max_length=15)
    invoice_id = models.CharField(max_length=30, db_index=True)
    description = models.TextField(null=True, blank=True)
    amount = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    quantity = models.IntegerField(default=0)
    discount = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    vat = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pp_invoice_items"


class PaymentLink(models.Model):
    ref = models.CharField(max_length=30, unique=True)
    brand_id = models.CharField(max_length=15, db_index=True)
    product_info = models.TextField()  # JSON
    amount = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    quantity = models.IntegerField(default=0)
    currency = models.TextField()
    expired_date = models.TextField()
    status = models.CharField(max_length=8, default="active")
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pp_payment_link"


class PaymentLinkField(models.Model):
    paymentLinkID = models.CharField(max_length=30, db_index=True)
    formType = models.TextField()
    fieldName = models.TextField()
    value = models.TextField()
    required = models.CharField(max_length=5, default="true")
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pp_payment_link_field"


class Permission(models.Model):
    brand_id = models.CharField(max_length=15)
    a_id = models.CharField(max_length=15, db_index=True)
    permission = models.TextField()  # JSON {resources:{}, pages:{}}
    status = models.CharField(max_length=8, default="active")
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pp_permission"
        indexes = [
            models.Index(fields=['brand_id']),
        ]


class SmsData(models.Model):
    source = models.CharField(max_length=3, default="web")
    device_id = models.CharField(max_length=15)
    sender = models.CharField(max_length=15, default="--")
    sender_key = models.CharField(max_length=15)
    simslot = models.TextField(null=True, blank=True)
    number = models.CharField(max_length=20, default="--")
    amount = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    currency = models.CharField(max_length=10, default="--")
    trx_id = models.CharField(max_length=100, default="--")
    balance = models.CharField(max_length=70, default="--")
    message = models.TextField(null=True, blank=True)
    reason = models.TextField(null=True, blank=True)
    type = models.CharField(max_length=10, default="Personal")
    entry_type = models.CharField(max_length=9, default="automatic")
    edit_status = models.CharField(max_length=7, default="pending")
    status = models.CharField(max_length=15, default="approved")
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pp_sms_data"
        indexes = [
            models.Index(fields=['sender_key', 'trx_id']),
            models.Index(fields=['status']),
            models.Index(fields=['device_id']),
            models.Index(fields=['type']),
        ]


class Transaction(models.Model):
    brand_id = models.CharField(max_length=15, db_index=True)
    source = models.CharField(max_length=20, default="api")
    ref = models.CharField(max_length=30, unique=True)  # pp_id
    customer_info = models.TextField()  # JSON {name,email,mobile}
    amount = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    processing_fee = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    discount_amount = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    local_net_amount = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    currency = models.TextField(null=True, blank=True)
    local_currency = models.TextField(null=True, blank=True)
    sender = models.CharField(max_length=50, default="--")
    trx_id = models.CharField(max_length=70, default="--")
    trx_slip = models.TextField(null=True, blank=True)
    gateway_id = models.CharField(max_length=50, default="--")
    sender_key = models.CharField(max_length=50, default="--")
    sender_type = models.CharField(max_length=11, default="")
    source_info = models.TextField(null=True, blank=True)  # JSON [{label,value}]
    metadata = models.TextField(null=True, blank=True)  # JSON
    status = models.CharField(max_length=9, default="initiated")
    return_url = models.TextField(null=True, blank=True)
    webhook_url = models.TextField(null=True, blank=True)
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pp_transaction"
        indexes = [
            models.Index(fields=['trx_id']),
            models.Index(fields=['sender_key']),
            models.Index(fields=['status']),
            models.Index(fields=['gateway_id']),
            models.Index(fields=['source', 'status']),
            models.Index(fields=['sender']),
        ]


class WebhookLog(models.Model):
    ref = models.CharField(max_length=15)
    brand_id = models.CharField(max_length=15)
    payload = models.TextField()  # JSON
    url = models.TextField()
    attempts = models.IntegerField(default=0)
    response_body = models.TextField(null=True, blank=True)
    http_code = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=9, default="pending")
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pp_webhook_log"
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['ref']),
            models.Index(fields=['brand_id']),
        ]
