# PipraPay → Django (`piprapay-django`)

End-to-end **Python-Django port** of the PHP original at [`../PipraPay`](../PipraPay)
(PipraPay — open-source self-hosted payment-automation platform, AGPL-3.0).
Target: **100% functional comparison** — every route, table, gateway, admin page,
API contract, SMS parser, cron step and theme string.

## Quickstart

```bash
pip install -r requirements.txt   # Django, requests, Pillow, cryptography
python manage.py check_requirements
python manage.py migrate
python manage.py pp_install        # seeds admin + Default brand + BDT + cron key
python manage.py test tests
python manage.py runserver 0.0.0.0:8080
```

Or seed manually via shell (same rows the PHP installer writes):

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
| `login` / `forgot` / `2fa` (+`?logout`) | `/login/` `/forgot/` `/2fa/` (+ global `?logout`) | ✅ |
| `ipn/<gateway_id>` (empty → 400) | `/ipn/<gateway_id>/`, `/ipn/` → 400 | ✅ |
| `api/checkout/redirect` | `/api/checkout/redirect/` | ✅ |
| `api/checkout/popup` | `/api/checkout/popup/` | ✅ |
| `api/verify-payment` | `/api/verify-payment/` | ✅ |
| `api/refund-payment` | `/api/refund-payment/` | ✅ |
| unknown `api_type` | `/api/<anything>/` → JSON error | ✅ |
| companion (`action-companion`) | `/api/companion/<action>/` | ✅ |
| `payment/<pp_id>` + `?gateway=` + `?pp_callback` | `/payment/<pp_id>/`, `/payment/<pp_id>/verify/`, `/payment/<pp_id>/callback/` | ✅ |
| `invoice/<ref>` + `invoice/webhook` | `/invoice/<ref>/`, `/invoice/<ref>/pay/`, `/invoice/webhook/` | ✅ |
| `payment-link/<ref>[/<brand>]` + `default` | `/payment-link/<ref>/[/<brand>/]`, `/payment-link/default/<brand>/`, `…/submit/` | ✅ |
| `admin/*` (≈40 pages incl. addons, info JSON) | `/admin/*` (`staff/urls.py`) | ✅ |
| `set-default-brand` | `/set-brand/` (POST) | ✅ |
| `cron/<token>` | `/cron/<token>/` + `run_cron` cmd | ✅ |
| install wizard | `/install/` + `pp_install` cmd | ✅ |
| `homepageRedirect` | `/` → login/host | ✅ |
| legacy `?page=X` links | auto-redirect shim | ✅ |
| `404` / maintenance / requirement | `page_404`, maintenance middleware, `check_requirements` | ✅ |

### Database (23/23 tables, same `pp_*` names)

`pp_addon`, `pp_addon_parameter`, `pp_admin`, `pp_api`, `pp_balance_verification`,
`pp_brands`, `pp_browser_log`, `pp_currency`, `pp_customer`, `pp_device`, `pp_domain`,
`pp_env`, `pp_faq`, `pp_gateways`, `pp_gateways_parameter`, `pp_invoice`,
`pp_invoice_items`, `pp_payment_link`, `pp_payment_link_field`, `pp_permission`,
`pp_sms_data`, `pp_transaction`, `pp_webhook_log` — all in `core/models.py`.

### Gateways (48/48, same slugs)

13 API · 30 automation · 5 manual. Full list in `gateways/plugins.py:SPECS`
(colors/fields verified against source).
Bespoke exact HTTP implementations in `gateways/api_gateways.py`: Stripe
(session + payment-intent re-check), SSLCommerz (validator API), OxaPay
(invoice + amount-equality callback + HMAC IPN), NOWPayments (coin picker,
tksort HMAC, confirmed/finished + duplicate guards), aamarPay (trxcheck
`status_code==2` + `opt_a`), bKash-tokenized (grant/create/execute +
`-BP-` match + full refund with paymentId extraction, multi-base fallback
and status polling), EPS (HMAC token + initialize + `ValueA` check),
Nagad-merchant-API (RSA init/complete/verify via `cryptography`, order map),
PathaoPay-merchant-API (reference-token header + capture), PayPal
(order match ±0.01, capture, COMPLETED), PayStation (transaction-status
verify), shurjoPay (return-via-IPN + verify + cancel marking), Binance
(order-id form + signed `sapi/v1/pay/transactions` USDT match).
Automation/manual gateways use verbatim `lang_text` + `instructions()` from
`gateways/lang_data.json` with the duplicate-trxid guard, `pending_payment`
mobile flow, slip upload (≤5MB) and webhook dispatch from
`transaction-verify`.

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

- Money: `bcscale(8)` → `Decimal` 8dp (`core/money.py`); `money_round` truncates
  like bc; IDs are digits-only; tolerance is the range
  `[checkout, checkout+tolerance]`; domain/expiry/date helpers mirror PHP edge
  cases (`www.` strip, EOD dates, unparseable expiry → True).
- `pp_set_transaction_status()` → `core/transactions.set_transaction_status()` (initiated-only guard, webhook queue, `transactions.updated` hook).
- `transaction-verify` → `verify_transaction_post()` + `POST /payment/<id>/verify/`
  (FX/fee math first, duplicate guard, SMS+tolerance → completed, `pending_payment`
  mobile flow, manual trxid/slip, Code 101-106 JSON).
- Invoice pay + `invoice/webhook` receiver + payment-link submit/default flows
  mirror `action-v2` (stock decrement, expiry display, custom fields, file upload).
- `MFSMessageVerified()` → `core/sms_engine.mfs_message_verified()` (exact PHP
  regexes; telecash/ipay have none, like PHP).
- `senderWhitelist()` → `sender_whitelist()` (same 11 senders).
- `reconcileByLongestChain()` → full BFS algorithm (`moneyToInt`/`intToMoney` semantics).
- `sendIPN/Multi` (curl_multi) → `ThreadPoolExecutor + requests`.
- Cron 6 steps in identical order (`cron_app/tasks.py`), same lock-then-success
  shape, `beta` default channel, PHP `version_compare`, `txn.ref` webhook keys.
- Auth: email-or-username, permission-row gate, `temp_password` fallback,
  `reset_limit` + `forgot.password` hook, 2FA window 2, `pp_brand` scoping.
- Companion device API (`/api/companion/<action>/`): OTP pairing, account info,
  bulk SMS ingest with balance-gated approve/review + reconcile, sender list, delete.
- Hooks `add_action/do_action/add_filter/apply_filters` → `core/hooks.py`.
- Twenty-six theme: 9 fields, 5 languages, full 69-key pack (`themes/lang_data.json`).
- System update (check/download/install with sha1 + backup + `.maintenance`) and
  module import (zip + `sql.sql`) in `staff/system_ops.py`; image/backup/zip/SQL
  helpers in `core/storage.py` (Pillow conversion mirrors Imagick).

## Layout

```
config/        settings/urls/wsgi/asgi
core/          23 models, money/env/permissions/hooks/transactions/sms_engine/webhooks/totp/views/middleware/storage + install/requirements commands
gateways/      base.py, plugins.py (48 specs), lang_data.json (verbatim text), api_gateways.py (13 HTTP ports), registry.py, ipn_views.py, bkash_refund.py
checkout/      payment/invoice (+pay, webhook)/payment-link (+submit, default)/verify/receipt views
api/           checkout+verify+refund views, companion device API
staff/         admin panel views/urls/auth + system_ops.py (update/import/addons)
cron_app/      tasks.py, views.py, run_cron command
themes/        twenty_six.py + lang_data.json (69 keys)
templates/     base, core/, checkout/, staff/
tests/         test_parity.py + test_parity2.py + test_parity3.py + test_gateways.py (94 tests)
tools/         extract_gateway_lang.py (regenerates lang_data.json from PHP)
AGENTS.md      agent operating manual
```

## License

AGPL-3.0, same as the original. Modified versions distributed must stay open-source
under AGPL-3.0.
