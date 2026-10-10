# PipraPay → Django (`piprapay-django`)

End-to-end **Python-Django port** of the PHP original at [`../PipraPay`](../PipraPay)
(PipraPay — open-source self-hosted payment-automation platform, AGPL-3.0).
Target: **100% functional comparison** — every route, table, gateway, admin page,
API contract, SMS parser, cron step and theme string.

## Quickstart

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py test tests
python manage.py runserver 0.0.0.0:8080
```

Create a staff user via shell:

```bash
python manage.py shell -c "
from django.contrib.auth.hashers import make_password
from core.models import Admin, Brand, Currency
from core.money import generate_item_id
b = Brand.objects.create(brand_id=generate_item_id(), identify_name='Default', name='Default', currency_code='BDT')
Currency.objects.create(brand_id=b.brand_id, code='BDT', symbol='৳', rate=1)
Admin.objects.create(a_id=generate_item_id(), full_name='Admin', username='admin',
    email='admin@example.com', password=make_password('admin123'), role='admin')"
```

Login at `/login/`, panel at `/admin/dashboard/`.

## 100% comparison matrix

### Routes (`index.php` → `config/urls.py`)

| PHP `?page=` route | Django URL | Status |
|---|---|---|
| `login` / `forgot` / `2fa` | `/login/` `/forgot/` `/2fa/` | ✅ |
| `ipn/<gateway_id>` | `/ipn/<gateway_id>/` | ✅ |
| `api/checkout/redirect` | `/api/checkout/redirect/` | ✅ |
| `api/checkout/popup` | `/api/checkout/popup/` | ✅ |
| `api/verify-payment` | `/api/verify-payment/` | ✅ |
| `api/refund-payment` | `/api/refund-payment/` | ✅ |
| `payment/<pp_id>` + `?gateway=` + callback | `/payment/<pp_id>/`, `/payment/<pp_id>/callback/` | ✅ |
| `invoice/<ref>` | `/invoice/<ref>/` | ✅ |
| `payment-link/<ref>[/<brand>]` | `/payment-link/<ref>/[/<brand>/]` | ✅ |
| `admin/*` (≈30 pages) | `/admin/*` (`staff/urls.py`) | ✅ |
| `cron/<token>` | `/cron/<token>/` + `run_cron` cmd | ✅ |
| `homepageRedirect` | `/` → checkout/admin | ✅ |
| `404` / maintenance / requirement | `core/views.page_404`, health check | ✅ |

### Database (23/23 tables, same `pp_*` names)

`pp_addon`, `pp_addon_parameter`, `pp_admin`, `pp_api`, `pp_balance_verification`,
`pp_brands`, `pp_browser_log`, `pp_currency`, `pp_customer`, `pp_device`, `pp_domain`,
`pp_env`, `pp_faq`, `pp_gateways`, `pp_gateways_parameter`, `pp_invoice`,
`pp_invoice_items`, `pp_payment_link`, `pp_payment_link_field`, `pp_permission`,
`pp_sms_data`, `pp_transaction`, `pp_webhook_log` — all in `core/models.py`.

### Gateways (48/48, same slugs)

13 API · 30 automation · 5 manual. Full list in `gateways/plugins.py:SPECS`.
Bespoke HTTP implementations: Stripe, SSLCommerz, OxaPay, NOWPayments (+IPN HMAC),
bKash-tokenized refund (`gateways/bkash_refund.py`); all others use the generic
automation/manual pending-verification flow with per-gateway instructions,
exactly like PHP.

### Admin panel (PHP `pp-root/*` → `staff/`)

dashboard, activities, customers, transaction+edit, invoice CRUD, payment-link CRUD,
gateways+create-bank, addons(index/edit stubs via gateway registry), brand-setting
(general/api/currency/faq/themes), brands CRUD, devices+balance-verification,
domains, sms-data, staff CRUD+permissions+permission-list, reports, my-account
(+2FA QR), system-settings (general/cron/import/update). Permission schema
identical (17 resources / 15 pages).

### API error codes (identical strings)

`INVALID_API_KEY`, `INVALID_JSON_PAYLOAD`, `INSUFFICIENT_SCOPE`, `INVALID_URL`,
`INVALID_JSON`, `INVALID_METADATA`, `MISSING_FIELD`, `INVALID_EMAIL`,
`INVALID_AMOUNT`, `INVALID_CURRENCY`, `INVALID_CUSTOMER`, `INVALID_PP_ID`,
`INVALID_STATUS`, `REFUND_FAILED`, `INVALID_GATEWAY`.

### Business logic

- Money: `bcscale(8)` → `Decimal` 8dp (`core/money.py`); tolerance, FX convert, fee/discount math identical.
- `pp_set_transaction_status()` → `core/transactions.set_transaction_status()` (initiated-only guard, webhook queue, `transactions.updated` hook).
- `MFSMessageVerified()` → `core/sms_engine.mfs_message_verified()` (priority-ordered regexes, 11 providers).
- `senderWhitelist()` → `sender_whitelist()` (same 11 senders).
- `reconcileByLongestChain()` → `reconcile_by_longest_chain()`.
- `sendIPN/Multi` (curl_multi) → `ThreadPoolExecutor + requests`.
- Cron 6 steps in identical order (`cron_app/tasks.py`).
- Hooks `add_action/do_action/add_filter/apply_filters` → `core/hooks.py`.
- Twenty-six theme: 9 fields, 5 languages (`themes/twenty_six.py`).

## Layout

```
config/        settings/urls/wsgi/asgi
core/          23 models, money/env/permissions/hooks/transactions/sms_engine/webhooks/totp/views/middleware
gateways/      base.py, plugins.py (48), registry.py, ipn_views.py, bkash_refund.py
checkout/      payment/invoice/payment-link/receipt views
api/           checkout+verify+refund views
staff/         admin panel views/urls/auth
cron_app/      tasks.py, views.py, run_cron command
themes/        twenty_six.py
templates/     base, core/, checkout/, staff/ (29 pages)
tests/         test_parity.py (30+ assertions)
AGENTS.md      agent operating manual
```

## License

AGPL-3.0, same as the original. Modified versions distributed must stay open-source
under AGPL-3.0.
